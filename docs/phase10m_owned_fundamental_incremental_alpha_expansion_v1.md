# Phase 10-M — Owned Fundamental Incremental Alpha Expansion (v1)

## 1. Why this phase exists

Phase 10-L-B exhausted the **two-factor reweighting** path: no reweight / z-cap / winsorize / liquidity
/ sector-cap of the frozen `composite_sn` (long `fcf_to_assets`, short `operating_accruals`, equal-weight,
sector-neutral, 63d) beat the baseline on honest evidence (`REJECT_REWEIGHTING_OVERFIT`). The next
untested question — flagged by the 10-C / 10-D "next plan" — is whether adding a **third owned
fundamental factor** (a profitability, investment, or leverage-risk leg) adds *incremental* alpha beyond
the two-leg baseline.

Phase 10-M answers that, honestly and narrowly. It does **not** re-test two-factor reweighting
(exhausted), **not** add providers, **not** make live API calls, **not** run a broad alpha search,
**not** optimise weights, and **not** invent fields. Every candidate leg is an owned EODHD fundamental
factor, built PIT-safe from the **same filing-date line items** the baseline legs use
(`b10._fund_quarters`), and scored with the **exact** 10-D engine (`c10._eval`, `d10.quarterly_backtest`,
`d10.walk_forward_h`) so it is directly comparable.

## 2. Inputs (owned / local; offline)

- The Norgate survivorship-free earnings-event panel (`c10.build_panel`, 38,725 events / 545 tickers) —
  the same panel 10-B/10-C/10-D use.
- The owned EODHD fundamentals cache (`research/data/eodhd/raw/fundamentals`, 547 tickers) for the
  reconstructed factors, and the already-normalized 10-B family CSVs
  (`research/data/eodhd/normalized/...`) for the pre-built factors.
- The frozen 10-D baseline JSON — the panel-integrity guard.

No `build_panel` over live data, no network, **no live API calls**, no key, no provider probe.

## 3. Candidate factors (8; pre-declared; a-priori orientation)

| factor | family | orient | source | definition |
|---|---|---|---|---|
| `gross_profitability` | profitability | +1 | normalized (10-B) | gross profit / assets (Novy-Marx) |
| `return_on_assets` | profitability | +1 | reconstructed | net income / assets |
| `operating_margin` | profitability | +1 | reconstructed | operating income / revenue |
| `cash_return_on_assets` | profitability | +1 | reconstructed | operating cash flow / assets |
| `asset_growth` | investment | −1 | normalized (10-B) | yoy total-asset growth (Cooper-Gulen-Schill) |
| `net_share_issuance` | investment | −1 | normalized (10-B) | yoy shares-outstanding growth (issuance) |
| `leverage_change` | leverage | −1 | normalized (10-B) | change in debt/assets |
| `debt_to_assets` | leverage | −1 | reconstructed | total debt / assets (level; low-leverage = safer) |

The reconstructed factors are ratios of the **same** filing-date line items `b10._fund_quarters`
already extracts for the baseline legs (`net_income`, `operating_income`, `total_revenue`,
`total_assets`, `total_debt`, `cfo`) — no new fields, same PIT availability rule (filing date, 90-day
fallback lag). Orientations follow standard quality-factor convention (profitable +, investing / issuing
/ levering −) and are fixed **before** the run; a wrong-signed factor simply fails the standalone screen.

Value / yield factors (`earnings_yield`, `book_to_market`, `fcf_yield`) and multi-year stability factors
are **not** reconstructed here — they require a PIT market-cap / equity price join or long history not in
the current filing-date line-item extraction. This is documented as an implementation limit and the
clearest owned-data gap for a later dedicated phase.

## 4. Method

1. Build the panel + baseline legs and rebuild `composite_sn` (`d10.build_composite`). **Integrity
   guard:** `composite_sn` must reproduce the frozen 10-D baseline within tolerance (IC t 0.25,
   net-25/50bps 0.0015, turnover 0.10) or the phase returns `NEEDS_FACTOR_INPUT_REPAIR` with no scoring.
2. Reconstruct the 4 non-normalized factors, attach all 8 via `y8.attach_orthogonal_feature` (as-of
   `available_date <= entry_date`, no lookahead).
3. **Standalone screen (reject weak before composite testing):** a factor is eligible only if, at 63d
   oriented, mean IC is positive **and** both cohorts (old/new) are oriented-positive **and** both
   subperiods (pre/post-2020) are oriented-positive. This rejects wrong-signed / sign-unstable factors;
   it does not require *strength* (a modest but directionally-robust leg can still diversify a
   composite — the relative beat test is the real arbiter).
4. **Composite test:** for each eligible factor, blend baseline + factor at the allowed weights only
   (baseline 70 / new 30, 60/40, 50/50) in within-month z space; plus two-factor blends (60/20/20,
   50/25/25) for the top-2 eligible factors by standalone IC t. Score each on the factor's common
   support against a **matched baseline** (`composite_sn` on the identical rows) so coverage never
   confounds the comparison.
5. **Strict relative champion test** (identical discipline to 10-L-B): a variant beats the matched
   baseline only if net-25bps strictly up, net-50bps not worse, turnover not materially worse (≤1.10×;
   hard reject >1.50×), IC t ≥ base − 0.10, OOS frac-positive ≥ base, top-sector share ≤ base, **and**
   robust (both cohorts +, both subperiods +). Any in-sample net25 gain that fails a secondary criterion
   is `REJECT_INCREMENTAL_OVERFIT`, not a champion.

## 5. Decision enum

- `INCREMENTAL_ALPHA_READY_FOR_PAPER_RULES` — a blended variant clears the strict relative test.
- `BASELINE_REMAINS_CHAMPION` — no eligible factor's blend beats the matched baseline (or none eligible).
- `REJECT_INCREMENTAL_OVERFIT` — a variant raises in-sample net25 but fails a secondary criterion.
- `NEEDS_FACTOR_INPUT_REPAIR` — the panel fails the 10-D reproduction guard (no scoring).
- `NEEDS_MORE_OWNED_DATA` — reserved for when the owned quality-family branch is provably exhausted and
  only a new owned data family (e.g. a value leg) could move the needle.

## 6. Result (this run)

**Decision: `BASELINE_REMAINS_CHAMPION`.** The panel reproduced the frozen 10-D baseline exactly
(63d IC t 2.665, net25 +0.00401, net50 +0.00095, turnover 0.6115 — `reproduces_within_tolerance=True`).

Of the 8 candidate factors, **7 failed the standalone directional screen** and only 1 was eligible:

- `return_on_assets`, `operating_margin`, `leverage_change` — **wrong-signed** at 63d vs their a-priori
  anomaly (oriented mean IC negative). These are honest failures, not tuning.
- `gross_profitability`, `net_share_issuance`, `debt_to_assets` — **sign-unstable** (the oriented IC sign
  disagrees across old/new cohorts or pre/post-2020 subperiods).
- `cash_return_on_assets` — **eligible** (63d IC +0.0389, t 2.44, both cohorts +, both subperiods +): a
  genuine cash-quality signal.

Blending the one eligible factor (`cash_return_on_assets`) at 70/30, 60/40, 50/50 **raised IC t**
(2.76 / 2.84 / 2.85 vs baseline 2.66) but **lowered** quarterly net-25bps (0.00272 / 0.00186 / 0.00232 vs
baseline 0.00401) — a classic *IC-up, net-spread-down* dilution: the cash-quality leg is correlated with
the FCF long leg and erodes the cost-efficient quintile spread carried by the accruals short leg. No
variant beat the matched baseline; no two-factor blend was possible (only one eligible factor). The
baseline stays champion; no widened composite is productized.

## 7. Why the alpha remains modest / boundary

Nothing here strengthens the baseline. The two-leg `composite_sn` remains a **modest / boundary** alpha:
63d IC t ≈ 2.665 (below the project's 3.0 strong bar), net-of-cost quarterly edge small, the short
`operating_accruals` leg carrying most of the robustness. Adding a third owned quality leg did not raise
the cost-robust edge — if anything it diluted it. This is not oversold and is not a prediction oracle.

## 8. What this phase did **not** test

Two-factor reweighting (10-L-B, exhausted); value/yield or multi-year stability factors (need a price /
equity join — deferred); transforms / interactions of the owned factors (**Phase 10-N**); regime
conditioning (**Phase 10-O**); any new provider, paid feed, or live API. This phase makes **no live API
calls**, creates **no orders** and **no automation**, connects to no broker, writes nothing to the Paper
Trader, and does not deploy.

## 9. What comes next

`BASELINE_REMAINS_CHAMPION` → **Phase 10-N** (fundamental transformation and quality-value interaction
search) per the autonomous research queue. If 10-N and 10-O also fail, Phase 10-Q writes the final
owned-data-exhaustion decision.

## Artifacts (`research/output/phase10m_owned_fundamental_incremental_alpha_expansion/`)

`phase10m_owned_fundamental_incremental_alpha_expansion.json` · `factor_input_inventory.csv` ·
`standalone_factor_screen.csv` · `composite_variant_scorecard.csv` · `baseline_vs_variants.csv` ·
`oos_stability_report.csv` · `cohort_stability_report.csv` · `sector_concentration_report.csv` ·
`turnover_cost_report.csv` · `rejected_candidates.csv` · `reconstructed_factors/*.csv`.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python -m py_compile research/run_phase10m_owned_fundamental_incremental_alpha_expansion.py
python research/run_phase10m_owned_fundamental_incremental_alpha_expansion.py          # offline; no key
python -m pytest tests/test_phase10m_owned_fundamental_incremental_alpha_expansion.py -q
```

## Constraints honored

Offline (no network / key / provider probe); **owned/local data only** (Norgate panel + owned EODHD
fundamentals); **no new factor family beyond owned fundamentals; no broad alpha search; no weight
optimisation**; **no Paper Trader writes; no orders; no automation; no broker; no live trading;
no deploy; no GCP**; no package install; targeted tests only; output is research metadata only.
**No commit. No push.**
