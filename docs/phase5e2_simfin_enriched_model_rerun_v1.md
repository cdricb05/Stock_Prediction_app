# Phase 5-E2 — SimFin-Enriched Cross-Sectional Model Rerun (v1)

## Why this phase exists

Phase 5-C established a **price-only** cross-sectional alpha baseline (momentum /
relative-strength / volatility features, ridge + composite, walk-forward, rank
IC ≈ 0.035 for the deployable ridge). Phase 5-E1E then collected, locally and
cache-safely, SimFin Free quarterly fundamentals for ~105 of the 128-name Phase
5-C universe (100 standard + 5 bank-template names).

Phase 5-E2 answers **one** question with out-of-sample evidence:

> Does adding SimFin fundamentals to the price panel improve cross-sectional
> stock ranking enough to justify building a deployable scorer (Phase 5-F)?

This is **Track A quant research**. It trains nothing deployable, deploys
nothing, touches no Paper Trader / GCP code, writes nothing to `D:`, places no
orders, and persists no binary model artifact. It makes **no network calls and
no SimFin downloads** and needs **no API key** — it reads only the
already-collected local data.

## Inputs (all local / read-only)

| Input | Path | Use |
|-------|------|-----|
| Price history | `D:\…\phase2k_g_expanded_price_history_free.csv` | read-only; price panel + labels |
| Phase 5-C harness | `research/run_phase5c_cross_sectional_alpha_research.py` | imported and reused (panel, ridge, walk-forward, metrics) |
| SimFin fundamentals | `research/data/simfin/normalized/phase5e1e/{standard,banks}/{pl,bs,cf}.csv` | git-ignored 5-E1E statements |

The Phase 5-C runner is **imported, not duplicated**, so the price-only baseline
is reproduced under the exact same code path the enriched models run through.

## Point-in-time join (the leakage-critical part)

Each fundamental is attached to a price-panel row **by its data-availability
date**, never the fiscal period end:

- **availability date = SimFin `Publish Date`.** When `Publish Date` is null,
  fall back to the fiscal `Report Date` **+ 90 days** (a conservative 10-Q/10-K
  filing lag).
- A statement merges `pl`+`bs`+`cf` for the same `(ticker, fiscal year, fiscal
  period)`; its availability date is the **latest** publish date across the
  three (all three must be public to be usable).
- For each monthly rebalance date `d` and ticker, the join picks the **most
  recent** statement whose availability date is `<= d`. Statements available
  after `d` are never used (`max_lookahead_violation_days` must be `0`).
- A statement older than **400 days** at `d` is flagged **stale** (still used,
  but counted by the staleness gate).

The free tier is delayed ~12 months, so `usable_for_live_trading_today` is
always **false** — this is research/backtesting only. The 12-month delay does
**not** corrupt a historical backtest, because the publish date is the correct
as-of date for each past quarter.

## Fundamental features (computed internally — SimFin derived ratios absent)

Derived ratios were unavailable in the free tier, so every feature is computed
from raw statement line items. Banks use the **bank statement template** and a
separate feature subset; standard companies use the standard template. Features
out of a name's template are left null → neutral (z = 0) in standardization.

| Family | Features (scope) |
|--------|------------------|
| profitability | gross_margin *(std)*, operating_margin, net_margin, roa_ttm |
| growth | revenue / operating_income / net_income YoY (same fiscal period) |
| leverage | debt_to_assets *(std)*, liabilities_to_assets, equity_to_assets *(bank)* |
| liquidity / quality | current_ratio *(std)*, cash_to_assets *(std)*, ocf_to_assets, fcf_to_assets *(std)* |
| valuation | earnings_yield_ttm, book_to_price *(price × reported diluted shares; cross-sectional rank only)* |
| bank-specific | deposits_to_assets, loans_to_assets *(bank)* |
| quality composite | mean of standardized net/operating margin, roa, ocf, fcf, −liabilities/assets |

Valuation uses `market cap ≈ adjusted_close × diluted shares`. This is an
approximation (adjusted close vs as-reported shares); it is used **only** for
cross-sectional ranking and is standardized per date, so the absolute scale is
irrelevant.

## Models (one identical walk-forward harness)

All variants are scored with the **same** ridge, walk-forward (yearly folds,
train-on-past / test-on-future, ≥20-session embargo) so the only difference is
the feature set → clean incremental attribution:

1. `price_only_full_panel_reference` — price features, full panel (continuity with 5-C).
2. `price_only_baseline` — price features, **common set** (rows with PIT fundamentals).
3. `fundamentals_only` — fundamental features, common set.
4. `price_plus_fundamentals` — union, common set.
5. `price_plus_fundamentals_quality` — union + quality composite, common set.

**Incremental edge** = model (4) − model (2) on the common set (Δ mean rank IC,
Δ t-stat, Δ decile spread, per-year IC deltas, fraction of years improved). A
label-shuffle **placebo** confirms the combined model's IC collapses toward 0.

## Run it

```powershell
python research\run_phase5e2_simfin_enriched_model_rerun.py
# optional: --price-csv <path>  --data-dir <normalized dir>  --max-tickers N
```

No key, no network, no SimFin download. Reads `D:` price history (read-only) and
the local SimFin normalized CSVs.

## Recommendations (the six allowed values)

| Value | When |
|-------|------|
| `READY_FOR_PHASE5F_DEPLOYABLE_SCORER` | fundamentals add stable, significant incremental IC (Δ ≥ 0.01, combined t-stat ≥ 2 and ≥ baseline, decile spread up, not stale) |
| `FUNDAMENTALS_IMPROVE_BUT_NOT_DEPLOYABLE` | fundamentals improve ranking (Δ > 0) but not enough / not stable |
| `NO_INCREMENTAL_EDGE_USE_PRICE_ONLY` | fundamentals do not improve ranking; keep price-only |
| `DATA_COVERAGE_BLOCKER` | price history or SimFin data missing, or too few PIT names/dates |
| `PIT_SAFETY_BLOCKER` | no point-in-time-safe availability date could be established |
| `ERROR` | unexpected failure (an honest ERROR report is still written) |

## Artifacts (committed-safe, under `research/output/phase5e2_simfin_enriched_model_rerun/`)

- `phase5e2_simfin_enriched_model_rerun.json` — full report (coverage, PIT rule,
  scoreboard, incremental edge, gate matrix, recommendation, safety contract).
- `simfin_enriched_feature_catalog.csv` — every feature: family, scope, sign, formula.
- `simfin_enriched_panel_sample.csv` — most-recent common-set rows (summary sample).
- `simfin_enriched_model_scoreboard.csv` — all five models' OOS metrics.
- `simfin_enriched_ic_by_year.csv` — per-model IC by year.
- `simfin_enriched_decile_spread.csv` — per-model decile spread.
- `simfin_enriched_coverage_report.csv` — PIT fundamental coverage by year.
- `simfin_enriched_validation_gate_matrix.csv` — PASS/FAIL/WARNING gates.
- `simfin_enriched_incremental_edge_report.csv` — price-only vs price+fundamentals.
- `phase5f_deployable_scorer_plan.json` — next-phase plan, gated on the recommendation.

The git-ignored raw/normalized SimFin payloads under `research/data/simfin/` are
never copied into these artifacts.

## Result (v1 live run, 105 names / 69 PIT dates, 5 banks)

The honest answer is **no incremental edge**:

| Model (common set) | mean rank IC | IC t-stat | decile spread |
|--------------------|-------------:|----------:|--------------:|
| price_only_baseline | **0.0113** | 0.34 | 0.0156 |
| fundamentals_only | −0.0240 | −1.09 | 0.0027 |
| price_plus_fundamentals | 0.0044 | 0.14 | 0.0098 |
| price_plus_fundamentals_quality | 0.0053 | 0.18 | 0.0137 |

Δ mean rank IC (price+fundamentals − price-only) = **−0.0069** → adding SimFin
fundamentals **did not** improve ranking; it slightly hurt it. Placebo IC ≈
−0.012 (≈ 0, no leakage); PIT lookahead violations = 0; coverage ≈ 80% from 2021
onward, 0% before 2020 (correct — no statements were public then).

**Recommendation: `NO_INCREMENTAL_EDGE_USE_PRICE_ONLY`.** Likely drivers: the
free tier limits the panel to ~2021+ (where the price-only IC is itself weak and
noisy), the 12-month delay forces stale alignment, and 20-day forward ranking is
dominated by price/momentum dynamics that quarterly fundamentals do not move.

## Safety contract

`preview_only=true`; `orders_enabled`, `automation_enabled`,
`broker_execution_enabled`, `production_replacement`, `writes_to_d_drive`,
`modifies_paper_trader`, `modifies_gcp`, `deploys`, `creates_binary_model_artifact`
all `false`. Models are fit in memory for evaluation only — nothing is persisted.
No FMP, no provider shopping, no live API calls, no SimFin downloads, no commit,
no push.
