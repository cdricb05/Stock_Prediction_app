# Phase 3-R — Macro/Inflation Regime Feature Layer + Walk-Forward Re-Test (v1)

## Purpose

Phase 3-R is a **research-only** phase that follows the Phase 3-Q robustness diagnosis. Phase 3-Q
showed the Phase 3-P best model — `ridge_technical_only` @ **126d** — is positive gross and
survives realistic **transaction cost**, but is **regime-fragile**: its 2021 worst-year rank IC
(−0.108) is a regime/style effect. The technical/momentum model earns its IC in volatile,
trending, **risk-off** markets and is weakest in calm **risk-on / low-volatility** years, and
2021 was almost entirely low-vol / risk-on. Phase 3-Q's #1 improvement priority was to add a
**macro / inflation / rates** regime feature family that encodes the rate/rotation regime the
technical model cannot see.

Phase 3-R attempts exactly that, using **only local, free, non-faked** data.
It **does not deploy**, **does not restart stock-api.service**, **does not enable**
`PREDICTOR_USE_MODEL_V2`, **does not run migrations**, **does not write to production DB**, and
**does not trade**. It
creates no production model candidate, computes no production predictions / scores / portfolio
weights / order instructions, writes no deployable model artifact (no pickle, no joblib dump, no
ONNX / HDF5 / Keras / Torch export), writes nothing to the D: drive, and calls **no provider /
paid-vendor / Alpha Vantage / FRED API**. It makes **no production edge** claim. The universe is
current-as-of, so every result remains **survivorship-biased**.

The single most important guardrail: **macro and sentiment data are never faked.** If no usable
local macro file exists, the phase **stops** with a blocked recommendation and writes the exact
data requirements — it never invents values.

## Phase 3-Q confirmation (gate)

Before any work the runner re-reads `phase3q_model_robustness_diagnostics.json` and confirms:
`phase == "3-Q"`; the recommendation is `MODEL_ROBUSTNESS_WEAK_FIXABLE_WITH_REGIME_AND_DATA` or
`MODEL_ROBUSTNESS_PASS_READY_FOR_RISK_SIMULATION`; `recommended_next_phase.phase == "3-R"`; and
`production_model_candidate_created`, `deployable_model_artifact_written`,
`production_predictions_computed`, `production_scores_computed`, `portfolio_weights_computed`,
`provider_api_called`, `alpha_vantage_called` are all `false`. If the confirmation fails the phase
returns `MACRO_REGIME_WALKFORWARD_BLOCKED_INPUTS`.

## Inputs (all read-only)

| Input | Use |
|---|---|
| `research/output/phase3q_model_robustness_diagnostics.json` | Phase 3-Q gate + Phase 3-P baseline facts (2021 IC, worst-year IC, stability, spread) |
| `research/output/phase3p_multisignal_walkforward_model.json` | reference |
| `research/output/phase3o_multisignal_feature_factory.json` | reference |
| `research/input/`, `research/output/`, `data/`, repo top-level CSVs | **local macro data scan** (no internet) |
| `D:\…\phase2k_g_expanded_price_history_free.csv` | price spine (D: **read-only**; header only when blocked) |
| `research/output/phase3l_.../aligned_feature_price_panel_universe.csv` | panel spine + forward labels |

## Methods

### 1. Macro data inventory (`macro_data_inventory.csv`)
Scans **local repo paths only** (`research/input`, `research/output`, `data/`, repo top-level
CSVs) for files whose name or columns hint at a macro family (CPI / inflation, fed funds /
policy rate, treasury / yield / yield-curve / real-rate, labor, oil, dollar, GDP/PMI). For each
candidate it records the detected family, columns, date range, whether it is **usable** (has a
recognized macro series column **and** a date column to align point-in-time), and a blocker. No
network call is made.

### 2. Macro feature registry (`macro_feature_registry.csv`)
The full preferred macro feature catalogue — `cpi_yoy`, `cpi_mom`, `inflation_acceleration`,
`inflation_regime_high_low`, `fed_funds_level`, `fed_funds_change_3m`,
`fed_policy_tightening_flag`, `treasury_10y`, `treasury_2y`, `yield_curve_10y_2y`,
`yield_curve_inversion_flag`, `real_rate_proxy`, `oil_return_63d`, `dollar_return_63d`,
`macro_risk_off_flag`, `macro_inflation_shock_flag`, `macro_rate_shock_flag` — with each
feature's family, frequency, **availability lag** rule, point-in-time safety, whether it is
**implemented** (only if the underlying local data exists), and its blocker.

### 3. Point-in-time availability rules
- Monthly macro releases are lagged conservatively. CPI is **not** available in the month it
  describes; if a release-date column is present it is used, otherwise a default **21-calendar-day
  availability lag** is applied.
- Treasury / fed-rate **daily** series are taken as-of by date when daily values exist.
- Derived features inherit the **slowest** input's lag (e.g. `real_rate_proxy` inherits the
  monthly CPI lag).
- The merge is `merge_asof(direction="backward")`: the last macro value available on/before the
  scoring date after applying the lag. **Future macro observations are never used for past
  scoring dates.**

### 4. Walk-forward re-test (only if usable macro data exists)
Rebuilds the Phase 3-P / 3-O feature panel **in memory** (reusing the Phase 3-Q OOS helpers,
which reuse Phase 3-P / 3-O, with the same expanding **walk-forward** folds and per-horizon
**embargo**), merges the macro features point-in-time, and compares five models —
`ridge_technical_only` (baseline), `ridge_combined_no_macro`, `ridge_combined_with_macro`,
`ridge_macro_interactions`, `regime_gated_macro_model` — on mean IC, 2021 IC, worst-year IC,
stability, decile spread, and cost survival. No production predictions, scores, weights, or
deployable artifacts are produced; only summary CSVs are written.

## Decision gates

| Recommendation | Condition |
|---|---|
| `MACRO_REGIME_WALKFORWARD_IMPROVES_ROBUSTNESS` | a macro model improves **both** worst-year and 2021 IC vs the Phase 3-P baseline, mean IC does not fall by more than 25%, and the decile spread stays positive |
| `MACRO_REGIME_WALKFORWARD_WEAK_NO_IMPROVEMENT` | macro data exists and the re-test runs but does not improve bad-year robustness |
| `MACRO_REGIME_WALKFORWARD_BLOCKED_NEEDS_LOCAL_MACRO_DATA` | no usable local macro/inflation/rates data file exists (stop; do **not** fake data) |
| `MACRO_REGIME_WALKFORWARD_BLOCKED_INPUTS` | Phase 3-Q result or a required local panel missing / unconfirmed |

`recommended_next_phase.phase` is always **`3-S`**: Sentiment and Earnings Coverage Expansion on
IMPROVES or WEAK; **Acquire Local Macro Data Files** on BLOCKED_NEEDS_LOCAL_MACRO_DATA; Repair
Phase 3-R Inputs on BLOCKED_INPUTS.

**This run (Phase 3-S, macro data now local):** `MACRO_REGIME_WALKFORWARD_WEAK_NO_IMPROVEMENT`
→ next phase **3-S — Sentiment and Earnings Coverage Expansion**. The five free FRED CSVs are now
present under `research/input/` (`macro_cpi_us.csv`, `macro_fed_funds.csv`,
`macro_treasury_yields.csv`, `macro_oil_wti.csv`, `macro_dollar_index.csv`), so the macro layer
was ingested, mapped, lagged point-in-time, and the walk-forward re-test ran on **real** data
(no value was faked). All 17 preferred macro features were implemented. The macro/regime models
**do** repair the bad year — `ridge_combined_with_macro` lifts the **2021 IC from −0.108 to
+0.027** and the **worst-year IC from −0.108 to −0.095** with a positive decile spread — but the
broader combined model's **mean IC falls from 0.082 to ~0.047 (> 25 %)**, so the strict IMPROVES
gate (which also requires mean IC to stay within 25 % of baseline) is not met. The honest verdict
is therefore **WEAK_NO_IMPROVEMENT**: macro features fix the regime-fragile bad year but at too
large a cost to average IC to claim a robustness win.

### FRED ingestion + column mapping (Phase 3-S)

The runner recognizes the FRED `observation_date` column and maps raw FRED series headers onto the
canonical macro namespace, converting `.`/blank placeholders to NaN (never fabricated):

| FRED header | Canonical | Family | Frequency | Availability rule |
|---|---|---|---|---|
| `CPIAUCSL` | `cpi_index_value` | inflation | monthly | observation date **+ 21-day** default lag |
| `FEDFUNDS` | `fed_funds_level` | policy_rate | monthly | observation date **+ 21-day** default lag |
| `DGS10` | `treasury_10y` | rates | daily | as-of by date |
| `DGS2` | `treasury_2y` | rates | daily | as-of by date |
| `DCOILWTICO` | `wti_price` | commodity | daily | as-of by date |
| `DTWEXBGS` | `broad_dollar_index` | fx | daily | as-of by date |

Monthly CPI / fed-funds derived features (`cpi_yoy`, `cpi_mom`, `inflation_acceleration`,
`inflation_regime_high_low`, `fed_funds_change_3m`, `fed_policy_tightening_flag`,
`macro_inflation_shock_flag`) are computed in observation order, then lagged to their availability
date before the as-of merge. Daily features (`yield_curve_10y_2y`, `yield_curve_inversion_flag`,
`real_rate_proxy`, `oil_return_63d`, `dollar_return_63d`, `macro_rate_shock_flag`,
`macro_risk_off_flag`) are computed on the panel's daily scoring-date grid after the merge, so no
future macro observation is ever used for a past scoring date. The macro × signal interactions
(`momentum_x_inflation_shock`, `momentum_x_rate_shock`, `volatility_x_yield_curve_inversion`,
`sector_relative_x_macro_risk_off`) are built per-row on the merged panel.

## Macro data requirements (reference; now satisfied locally)

Place the following **free, manually downloadable, non-paid** local files under
`research/input/`. This phase calls **no API**; download the CSVs separately and drop them in.

| Suggested path | Family | Needed columns | Free source examples | Frequency |
|---|---|---|---|---|
| `research/input/macro_cpi_us.csv` | inflation | `date`, `cpi_index_value` or `cpi_yoy`, optional `release_date` | FRED `CPIAUCSL`; BLS `CUUR0000SA0` | monthly |
| `research/input/macro_fed_funds.csv` | policy_rate | `date`, `fed_funds_rate` | FRED `DFF` / `FEDFUNDS` | daily/monthly |
| `research/input/macro_treasury_yields.csv` | rates | `date`, `treasury_10y`, `treasury_2y` | FRED `DGS10` + `DGS2`; US Treasury yield curve | daily |
| `research/input/macro_oil_wti.csv` (optional) | commodity | `date`, `wti_price` | FRED `DCOILWTICO` | daily |
| `research/input/macro_dollar_index.csv` (optional) | fx | `date`, `broad_dollar_index` | FRED `DTWEXBGS` | daily |

Coverage must span at least the Phase 3-P walk-forward window (~2019–2025). Data must be **real
(never faked)**, **free / non-paid**, and obtained by **manual download** (or with an API key the
user explicitly provides). When these files are present, re-running Phase 3-R will populate the
panel sample, scoreboard, yearly-stability, regime, and bad-year comparison CSVs and decide
IMPROVES vs WEAK.

## Outputs

Result JSON `research/output/phase3r_macro_regime_walkforward.json` plus, under
`research/output/phase3r_macro_regime_walkforward/`: `macro_data_inventory.csv`,
`macro_feature_registry.csv`, `macro_feature_panel_sample.csv`,
`macro_walkforward_scoreboard.csv`, `macro_yearly_stability.csv`, `macro_regime_performance.csv`,
`macro_bad_year_comparison.csv`, `macro_improvement_decision_table.csv`. On a blocked run the
model-comparison CSVs carry headers only (the bad-year comparison additionally carries the honest
Phase 3-P baseline carry-forward). Every file is **Git-safe (< 50 MB)**.

## Safety flags (result JSON)

`database_touched`, `database_write_executed`, `migration_executed`, `deployment_executed`,
`model_v2_enabled`, `production_edge_claimed`, `production_model_trained`,
`production_model_candidate_created`, `deployable_model_artifact_written`,
`production_predictions_computed`, `production_scores_computed`, `portfolio_weights_computed`,
`order_instructions_created`, `d_drive_written`, `provider_api_called`, `alpha_vantage_called`,
`paid_vendor_api_called`, `macro_faked`, `sentiment_faked` are all **false**; `no_trading`,
`no_orders`, `no_automation`, `research_only`, `labels_for_validation_only` are **true**. Macro
and sentiment data **are not faked** — when absent, the phase blocks rather than fabricating.

## Run

```powershell
# Windows PowerShell only.
python -B research\run_phase3r_macro_regime_walkforward.py
python -B tests\test_phase3r_macro_regime_walkforward.py
# Optional full end-to-end test into a temp dir:
$env:PHASE3R_LIVE = "1"; python -B tests\test_phase3r_macro_regime_walkforward.py
```
