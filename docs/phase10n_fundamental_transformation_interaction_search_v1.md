# Phase 10-N — Fundamental Transformation And Quality-Value Interaction Search (v1)

## 1. Why this phase exists

Phase 10-M tested owned fundamental factors as **linear** incremental legs and returned
`BASELINE_REMAINS_CHAMPION`. The next honest question: does a defensible **nonlinear transform** of the
owned factors — or an economically-motivated **interaction** between two owned factors — unlock signal
the linear form missed? Phase 10-N tests exactly that, narrowly.

It does **not** add providers, **not** make live API calls, **not** run a broad alpha search, **not**
optimise weights, **not** use ML / optimiser / genetic search, and **not** explode polynomials. Every
candidate is pre-declared and economically named, scored with the **exact** 10-D/10-M engine
(`c10._eval` / `d10.quarterly_backtest` / `d10.walk_forward_h`) against a matched baseline.

## 2. Inputs (owned / local; offline)

The Norgate survivorship-free panel (`c10.build_panel`), the owned EODHD normalized factor CSVs, and the
**10-M reconstructed factor CSVs** (reused so the fundamentals cache is not re-scanned). Value is the
panel-native `earnings_yield` column (8-series earnings-event build, PIT at the report). No network,
**no live API calls**, no key, no provider probe.

## 3. Candidates (pre-declared; capped)

**Transforms** (≤ 25): `signed-log`, within-month `rank`, sector-neutral (month×sector) `rank`,
year-over-year `delta`. Applied as:
- alternative-composite constructions (baseline 2-factor structure, transformed legs): `altcomp_signed_log`,
  `altcomp_rank`, `altcomp_snrank`;
- incremental transformed 3rd legs blended into `composite_sn` at the allowed 70/30, 60/40, 50/50 weights:
  `cash_roa_rank`, `gross_prof_snrank`, `fcf_delta`.

**Interactions** (≤ 10; each named + economically explained, standalone-screened, eligible ones blended):
- `quality × value` — cash_return_on_assets × earnings_yield;
- `profitability × investment` — gross_profitability × (−asset_growth);
- `accruals × leverage` — (−operating_accruals) × (−debt_to_assets);
- `FCF × value` — fcf_to_assets × earnings_yield.

## 4. Gates (strict, RELATIVE, with a subperiod-robustness guard)

Primary test (same discipline as 10-L-B / 10-M): a candidate beats the **matched** baseline
(`composite_sn` on the identical rows) only if net-25bps strictly up, net-50bps not worse, turnover not
materially worse (≤1.10×; hard reject >1.50×), IC t ≥ base − 0.10, OOS frac-positive ≥ base, top-sector
share ≤ base, and both cohorts + both subperiods IC-positive.

**Subperiod-robustness guard (added in 10-N):** any candidate that passes the full-sample test is
downgraded to `REJECT_OVERFIT` unless its net-25bps advantage over the matched baseline also holds (within
a small tolerance) in **both** the pre-2020 and post-2020 subperiods. This rejects one-era artifacts whose
full-sample gain reverses out-of-sample.

## 5. Decision enum

`TRANSFORMED_ALPHA_READY_FOR_PAPER_RULES` · `BASELINE_REMAINS_CHAMPION` · `REJECT_TRANSFORM_OVERFIT` ·
`NEEDS_TRANSFORM_INPUT_REPAIR` · `NEEDS_MORE_OWNED_DATA`.

## 6. Result (this run) — `REJECT_TRANSFORM_OVERFIT`

The panel reproduced the frozen 10-D baseline exactly (63d IC t 2.665, net25 +0.00401 — `reproduces=True`).

The most interesting finding — and why the decision is `REJECT_TRANSFORM_OVERFIT` rather than
`BASELINE_REMAINS_CHAMPION` — is **`altcomp_rank`** (the baseline composite with within-month
rank-transformed legs). On full-sample metrics it looked genuinely strong: **IC t 3.22** (clears the 3.0
strong bar; baseline 2.66), net25 0.0043 (>0.00401), net50 0.0012 (>0.00095), OOS pooled 0.0385, OOS
frac-positive 0.625, top-sector share 0.610 (lower than 0.626). It passed the full-sample strict relative
test on every axis.

**But the subperiod-robustness guard rejected it**, and an independent verification confirmed why: the
entire net25 advantage is a **pre-2020 relic that reverses out-of-sample** —

| period | `altcomp_rank` net25 | baseline net25 |
|---|---|---|
| full | 0.0043 | 0.0040 |
| **pre-2020** | **0.0096** | 0.0052 |
| **post-2020** | **0.0008** | **0.0033** |

Post-2020 (the recent, more relevant era) the rank composite is materially **worse** than the baseline,
and dropping the single most-favourable quarter turns the full-sample advantage negative. A rank-IC lift
concentrated in one era is not a tradeable, generalising alpha. This is exactly the "do not spin a
one-period result" discipline: the IC t 3.22 is real but era-bound, so it is **not** productized.

The other in-sample net25 improvers failed cleanly on secondary axes: `gross_prof_snrank` (IC t collapses
to 1.8–2.3 and OOS deteriorates), `fcf_delta` (sector concentration worsens to 0.63–0.64). Of the four
interactions, only `accruals × leverage` was directionally eligible, and its blends did not beat the
matched-baseline net25. No transform or interaction is a champion; the two-leg baseline stands.

## 7. Why the alpha remains modest / boundary

Nothing here strengthens the baseline in a way that generalises. The two-leg `composite_sn` remains a
**modest / boundary** alpha (63d IC t ≈ 2.665, below the 3.0 strong bar; small net-of-cost quarterly
edge; the short `operating_accruals` leg carrying most of the robustness). The one candidate that crossed
the strong IC bar did so only pre-2020. This is not oversold and is not a prediction oracle.

## 8. What this phase did **not** test

Regime conditioning (**Phase 10-O**); a properly-reconstructed EODHD-normalized value leg (deferred — the
panel-native `earnings_yield` was used only for interaction diagnostics); any new provider, paid feed, or
live API. This phase makes **no live API calls**, creates **no orders** and **no automation**, connects to
no broker, writes nothing to the Paper Trader, and does not deploy.

## 9. What comes next

`REJECT_TRANSFORM_OVERFIT` → **Phase 10-O** (regime and conditional alpha gating). If 10-O also fails,
Phase 10-Q writes the final owned-data-exhaustion decision.

## Artifacts (`research/output/phase10n_fundamental_transformation_interaction_search/`)

`phase10n_fundamental_transformation_interaction_search.json` · `transform_interaction_inventory.csv` ·
`interaction_standalone_screen.csv` · `transform_variant_scorecard.csv` · `baseline_vs_variants.csv` ·
`oos_stability_report.csv` · `cohort_stability_report.csv` · `sector_concentration_report.csv` ·
`turnover_cost_report.csv` · `rejected_candidates.csv` · `derived_factors/fcf_delta.csv`.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python -m py_compile research/run_phase10n_fundamental_transformation_interaction_search.py
python research/run_phase10n_fundamental_transformation_interaction_search.py          # offline; no key
python -m pytest tests/test_phase10n_fundamental_transformation_interaction_search.py -q
```

## Constraints honored

Offline (no network / key / provider probe); **owned/local data only**; **no ML / optimiser / genetic
search; no polynomial explosion; no new factor family; no weight optimisation**; **no Paper Trader writes;
no orders; no automation; no broker; no live trading; no deploy; no GCP**; no package install; targeted
tests only; output is research metadata only. **No commit. No push.**
