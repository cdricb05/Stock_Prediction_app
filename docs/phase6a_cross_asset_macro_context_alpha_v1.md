# Phase 6-A — Cross-Asset Macro Context Alpha Pack (v1)

**Track A (quant brain) research. Offline. Point-in-time. Walk-forward. Preview-only.**
Zero network, no provider call, no paid API, no API key read or required, no live data
fetched, no model trained or deployed, no database touched, no order/broker/automation,
no binary artifact, no Paper Trader / GCP / deploy work. No commit, no push.

## Why this phase exists

Phase 5-G2 showed the earnings-surprise / PEAD event composite carries **no** incremental
cross-sectional edge over the Phase 5-C price-only champion once coverage broadens
(`NO_INCREMENTAL_EVENT_EDGE`). The strategic read: the next lever is a **cross-market /
macro context** signal — conditioning single-stock selection on broader market forces —
not more single-name earnings wrappers.

    "Does broader market context improve 5-20 day equity selection beyond the Phase 5-C
     price-only champion, on the same dates and universe?"

## What this phase does

`research/run_phase6a_cross_asset_macro_context_alpha.py` is a reuse-first research
harness:

1. **Inventory.** Catalogs every local cross-asset / macro source and the global-ETF
   readiness already recorded by Phase 3-W / 3-X / 3-Y (read-only), then decides whether
   the **full multi-layer framework** can be tested locally.
2. **Reuses the Phase 5-C harness UNCHANGED** (import) for the price panel, the champion
   price-only reference, the walk-forward embargo logic, ridge, and the metric / placebo
   machinery — so every comparison is genuinely apples-to-apples on the same dates and
   universe.
3. **Builds a strictly-lagged macro factor pack** from the local FRED series + SPY and
   tests price-only vs macro-augmented cross-sectional rankers.
4. Emits an honest gate matrix and one allowed recommendation. It runs **no** strategy /
   shadow test and never forces a PASS.

## Data inventory — what is and isn't available locally

| Layer | Local source | Status |
|---|---|---|
| Energy (WTI crude) | `research/input/DCOILWTICO.csv` | available (daily) |
| Currency (broad USD) | `research/input/DTWEXBGS.csv` | available (daily) |
| Rates (10Y / 2Y / curve) | `research/input/fredgraph.csv` (DGS10, DGS2) | available (daily) |
| Inflation regime | `research/input/CPIAUCSL.csv` | available (monthly, slow) |
| Policy regime | `research/input/FEDFUNDS.csv` | available (monthly, slow) |
| Equity market (SPY) | D: price history (read-only) | available (daily) |
| **Credit (HYG/LQD)** | — | **missing** |
| **Volatility complex (VIX/VIXY)** | — | **missing** |
| **Equity structure (QQQ/IWM/DIA breadth)** | — | **missing** |
| **Global / regional (EFA/EEM/EWJ/FXI)** | — | **missing** |
| **Sector rotation (XLE..XLC)** | — | **missing** |
| **Full commodity + FX complex** | — | **missing (only WTI + broad USD)** |

The 22-ticker global cross-asset ETF pack (SPY..VIXY) is **not collected**: Phase 3-W
(manual intake) found 0 files and Phase 3-Y (Alpha Vantage collector) was halted by the
free-tier provider limit — **0 usable proxies across 0 asset classes** (`ready_threshold_met
= false`). So the rich multi-layer framework cannot be tested locally; only a **partial**
pack (oil / dollar / rates / SPY) can be built today.

## Feature pack (the partial pack actually built)

For each daily macro factor F ∈ {SPY, WTI oil, broad USD, 10Y, 2Y, 10Y-2Y curve}, all
evaluated at strict lag **t-1**:

- **Time-series factor features** (same for all names — context, not cross-sectional
  ranking): 5d / 20d change, 20d z-score *shock* (20d move / 60d-vol·√20), 60d realized vol.
- **Sensitivity** (`beta_F`): rolling 60d beta of each stock's daily return to factor F's
  daily change — *this* varies across names.
- **Macro shock × sensitivity interactions** (`ix_shock_F = beta_F · shock_F`,
  `ix_ret20_F = beta_F · ret20_F`).
- **Regime-state × sensitivity interactions**: risk-on/off (SPY) × β_SPY, rates-up/down ×
  β_10Y, dollar-up/down × β_USD, oil-shock × β_oil, inflation impulse (3m CPI) × β_oil,
  policy impulse (3m fed funds) × β_10Y.

Nothing is hardcoded as "oil up ⇒ USD up": every relationship is an estimated, lagged,
rolling sensitivity, standardized cross-sectionally per date before the ridge.

## Result (this run)

Common cross-section: **112 scored dates**, identical across all models.

| Model | mean rank IC | IC t-stat | incremental vs reference |
|---|---|---|---|
| `phase5c_reference` (champion = `top_quintile_score_model`) | **0.045229** | 1.88 | 0.0 (reference) |
| `price_only` (ridge baseline) | 0.035047 | 1.72 | −0.010181 |
| `macro_only` (ridge) | −0.019852 | −0.85 | −0.065080 |
| `price_plus_macro` (ridge) | **−0.005635** | −0.27 | **−0.050864** |
| `price_plus_macro_regime_interaction` (ridge) | −0.015938 | −0.76 | −0.061167 |

**Within-family attribution** (isolates macro's marginal effect vs the price-only **ridge**
baseline, removing the ridge-vs-logistic gap):

| | price-only ridge | + macro | within-family Δ |
|---|---|---|---|
| `price_plus_macro` | 0.035047 | −0.005635 | **−0.040683** |
| `price_plus_macro_regime_interaction` | 0.035047 | −0.015938 | −0.050986 |

- Best augmented model: `price_plus_macro`, IC −0.005635 (placebo −0.002813, leakage-clean).
- Incremental IC vs the same-universe Phase 5-C reference: **−0.050864** (threshold `>+0.005`).
- Sanity check: the price-only ridge reproduces Phase 5-C's own ridge IC (~0.035), confirming
  the Phase 6-A walk-forward faithfully mirrors the 5-C harness.

## Did macro context improve the signal? — **No.**

The local partial macro pack **did not** improve cross-sectional selection and in fact
**degraded** it: adding the macro sensitivities/interactions to the price-only ridge drops
IC from **+0.035 to −0.006** (a ~0.04 within-family loss), and macro-only ranks worse than
random. The rolling betas of single stocks to oil / USD / rates over 60d are noisy; their
cross-sectional standardization injects spurious tilts that the ridge over-fits in-sample
and that flip out-of-sample. Placebo ICs collapse toward zero, so this is a genuine
"no edge / negative edge", not a leakage artifact.

Crucially, this is a fair test only of the **partial** pack. The genuine multi-layer
hypothesis — credit stress, volatility regime, equity-structure breadth, global/regional
divergence, sector rotation, and the full commodity + FX complex — is **untestable locally**
because the cross-asset ETF pack has 0 usable proxies.

## Gates (this run): 13 PASS / 1 FAIL / 1 WARNING

- **FAIL** — `incremental_ic_over_reference_gate` (−0.050864 is not `> +0.005`): expected and
  correct for a no-edge result; the recommendation agrees with it.
- **WARNING** (not FAIL) — `full_framework_cross_asset_ready_gate`: 0 usable global ETF
  proxies; the full framework needs controlled collection to be tested fairly.
- All safety, leakage-clean, same-universe, no-network, and no-paid-API gates **PASS**.

## Recommendation: `NEEDS_CONTROLLED_CROSS_ASSET_DATA_COLLECTION`

The partial FRED+SPY pack does not confirm a macro edge, and the rich multi-layer framework
cannot be fairly tested without the cross-asset ETF universe (credit / vol / sector / global
/ full commodity + FX). The honest next step is **controlled, offline data collection**, not
a strategy test and not abandoning the hypothesis.

## Is Phase 6-B needed, and what should it be?

**Yes — as data collection, not a strategy test.** `phase6b_next_plan.json` records
`proceed_to_strategy_or_shadow_test = false`. Phase 6-B should collect the 22-ticker global
cross-asset ETF pack under the **existing offline collectors**, then rerun Phase 6-A with the
full multi-layer pack. Exact future commands (recorded, **not run** here):

```powershell
$env:ALPHAVANTAGE_API_KEY = "<key>"; python -B research\run_phase3y_alphavantage_global_etf_price_collector.py
python -B research\run_phase3w_manual_global_etf_data_import.py
python -B research\run_phase3x_manual_global_etf_pack_validation.py
```

READY threshold: ≥ 12 usable ETF proxies across ≥ 5 asset classes (Phase 3-X rule). Only once
READY is the full framework a fair test of the macro-context hypothesis.

## Committed-safe artifacts

`research/output/phase6a_cross_asset_macro_context_alpha/`:

- `phase6a_cross_asset_macro_context_alpha.json` — main report (all metrics + safety flags).
- `cross_asset_data_inventory.csv` — every local source + global-ETF readiness.
- `cross_asset_feature_catalog.csv` — factor / sensitivity / interaction / regime definitions.
- `cross_asset_model_scoreboard.csv` — per-model IC / t-stat / decile / placebo / incremental.
- `cross_asset_incremental_edge_report.csv` — per-model incremental edge vs the reference.
- `cross_asset_gate_matrix.csv` — gate / status / metric / value / threshold / note.
- `cross_asset_yearly_ic.csv` — yearly IC per model.
- `phase6b_next_plan.json` — gated next-step + controlled-collection plan (commands not run).

## Run commands

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python -B research\run_phase6a_cross_asset_macro_context_alpha.py
$env:PAPER_TRADER_TEST_DATABASE_URL = "postgresql+psycopg2://postgres:Adam2015@localhost:5432/paper_trader_test"
python -m pytest tests\test_phase6a_cross_asset_macro_context_alpha.py -q
```

## Safety contract

Offline · zero network / API call · no API key read or required · no live data fetched · no
raw file modified / staged · no D: write · no strategy / shadow test · committed-safe text
artifacts only · no model trained / deployed · no Paper Trader / GCP / deploy · no orders /
broker / automation · no binary artifacts · survivorship-biased universe (no production edge
claimed) · no commit · no push.
