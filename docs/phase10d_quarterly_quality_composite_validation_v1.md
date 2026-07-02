# Phase 10-D — Quarterly EODHD/Norgate Quality Composite Validation (v1)

## Purpose

Phase 10-C strictly out-of-sample validated the two owned-data quality leads as *standalone* monthly
signals and returned `QUALITY_ALPHA_REJECTED_NOT_COST_ROBUST`. Its decisive findings:

- Both leads (`fcf_to_assets` +1, `operating_accruals` −1) are **out-of-sample positive and
  cohort-stable** — *not* the 10-B overfit/decay failure mode.
- Their monthly (21d) edge died only because the earnings-**event** panel has ~99% month-to-month
  turnover — a **structural name-rotation artifact** (different names report in different months), not
  signal churn.
- Both **survive 25bps and 50bps at the 63d (quarterly) horizon**.

**Phase 10-D** builds the obvious next thing: a **transparent, equal-weight, quarterly-rebalanced
quality composite** of the two leads, and re-validates it **at the 63d horizon with a realistic
quarterly-cadence cost model**. It is **not** a data-acquisition, provider-search, Paper-Trader, order,
or automation phase. Fully **offline** (no network, no key, no provider probe); reuses the Phase 10-B
normalized CSVs and the Norgate **545-ticker / 38,725-event** panel via the Phase 10-C machinery (`c10`).

## Composite definition (transparent; fixed; no optimisation; no sign-flip)

| leg | orientation | transform | weight |
|---|---|---|---|
| `fcf_to_assets` | **+1** | within-month z of oriented level | 1.0 |
| `operating_accruals` | **−1** (Sloan) | within-month z of oriented (negated) level | 1.0 |

`comp_raw = z(o_fcf) + z(o_accruals)`; `comp_sn` is the same over the sector-neutral (within month×sector
de-meaned) legs. Z-scoring each leg within month already equalises signal variance, so **equal-weight of
z is equal-risk in signal space**; no separate local volatility/risk inputs exist, so **no optimised
equal-risk weighting is built** (that would be "optimised weights"). Orientations are **fixed a-priori**
from the documented anomaly — never flipped to fit the data. Both legs must be present for an event
(clean 2-factor intersection; coverage **38,404 / 38,725**).

## Method

- **Primary decision horizon: 63d (quarterly).** 1d/5d/21d/63d reported for context.
- **Walk-forward OOS** (24mo train / 6mo test / 6mo step, `equal` weighting — fixed orientation, pure
  held-out IC, no sign refit).
- **Realistic quarterly long-short backtest** (`quarterly_backtest`): group events by calendar quarter,
  one obs per ticker per quarter (latest), quintile long-short by composite, hold ~1 quarter, realise
  63d forward excess return. **Turnover is measured between consecutive quarters on the same recurring
  earnings names → genuine signal churn, not the month-to-month name rotation** that inflated the 10-C
  monthly turnover to ~0.99. Net spread = gross − bps·turnover·2 at 10/25/50 bps.
- Cohort (old/new), subperiod (pre/post-2020), leave-one-sector-out + concentration, liquidity filter,
  standalone-vs-composite comparison, and a monthly-vs-quarterly cost diagnostic.

## Acceptance gate (a-priori; never tuned)

`CONFIRMED` (paper-review-ready) requires, at 63d on `comp_raw`: IC t ≥ **3.0**; quarterly net-25bps > 0
**and** net-50bps > 0; both cohorts +; both subperiods +; sector-robust (top-share < 0.60 **and**
leave-one-out sign holds); sector-neutral composite +; high-liquidity +; OOS pooled > 0 with ≥ 60%
windows +. Rejection triggers checked first in order: cohort → sector → cost. `WEAK_BUT_WORTH_MONITORING`
if OOS-robust and cost-surviving but IC t in [2.0, 3.0) or net-50bps ≤ 0.

**Allowed decisions:** `QUARTERLY_QUALITY_COMPOSITE_CONFIRMED_READY_FOR_PAPER_REVIEW` ·
`..._WEAK_BUT_WORTH_MONITORING` · `..._REJECTED_NOT_COST_ROBUST` · `..._REJECTED_COHORT_INSTABILITY` ·
`..._REJECTED_SECTOR_INSTABILITY` · `EODHD_NORGATE_QUALITY_BRANCH_EXHAUSTED` ·
`HARD_BLOCKER_REQUIRES_USER_ACTION` · `ERROR_WITH_REPRO_COMMAND`.
**Forbidden:** `STRONG_ALPHA_FOUND_READY_FOR_REVIEW` without quarterly OOS proof, `MISSING_KEY`,
`NO_DATA`, `NEEDS_PROVIDER`, `EMPTY_PAYLOAD`, generic `ERROR`.

## Artifacts (19, in `research/output/phase10d_quarterly_quality_composite_validation/`)

`phase10d_quarterly_quality_composite_validation.json` · `composite_definition.csv` ·
`signal_input_inventory.csv` · `quarterly_horizon_validation_report.csv` ·
`monthly_vs_quarterly_cost_diagnostic.csv` · `walk_forward_ic_report.csv` ·
`walk_forward_decile_report.csv` · `transaction_cost_report.csv` · `turnover_report.csv` ·
`cohort_stability_report.csv` · `subperiod_stability_report.csv` · `sector_exclusion_report.csv` ·
`liquidity_filter_report.csv` · `standalone_vs_composite_comparison.csv` ·
`accepted_quarterly_quality_composite.csv` · `rejected_quarterly_quality_composite.csv` ·
`paper_review_candidate_package.csv` · `phase10e_next_plan.json` · `secret_safety_audit.csv`.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python -m py_compile research/run_phase10d_quarterly_quality_composite_validation.py
python -m pytest tests/test_phase10d_quarterly_quality_composite_validation.py -q   # targeted; 16 passed
python research/run_phase10d_quarterly_quality_composite_validation.py              # fully offline; no key
```

## Constraints honored

Offline (no network/key/provider probe); only `fcf_to_assets` + `operating_accruals` used; fixed equal
weights, no optimisation, no sign-flip; no FMP/AlphaVantage/Polygon/Finnhub; no Paper Trader; no GCP; no
orders; no automation; no deploy; no package install; no full regression; keys never printed or written.
**No commit. No push.**

---

## Status — live run 2026-06-29 (offline; exit 0)

**Final decision: `QUARTERLY_QUALITY_COMPOSITE_CONFIRMED_READY_FOR_PAPER_REVIEW`.**

The transparent equal-weight quarterly quality composite **clears the strict 63d gate** — the first
signal in the entire 8-T → 10-D arc to do so. It is a **legitimate but modest, boundary-level** pass and
is dispositioned to **human paper review** (paper-only, human-gated), *not* cleared for live trading.

### Composite vs standalones (63d)

| signal | IC t (63d) | OOS pooled (frac +) | qtr gross | qtr turnover | qtr net-25bps | qtr net-50bps |
|---|---|---|---|---|---|---|
| **composite_raw** | **3.07** | +0.0364 (0.625) | 0.0095 | 0.599 | **+0.0065** | **+0.0035** |
| fcf_to_assets | 2.53 | +0.0344 (**0.375**) | 0.0078 | 0.466 | +0.0054 | +0.0031 |
| operating_accruals | 3.07 | +0.0373 (0.688) | 0.0084 | 0.616 | +0.0053 | +0.0022 |

### What passed (every gate, genuinely)

- **63d IC t = 3.074 ≥ 3.0** (boundary).
- **Quarterly cost:** net-25bps **+0.0065**, net-50bps **+0.0035** — both positive (decision cost model =
  realistic quarterly turnover 0.599).
- **OOS walk-forward:** pooled +0.0364, **62.5%** of windows positive.
- **Cohort:** both positive — old IC t 1.51, **new IC t 2.17**. The **new cohort is *stronger*** — the
  opposite of the 10-B/8-V dilution-decay failure mode.
- **Subperiod:** both positive — pre-2020 t 2.91, post-2020 t 1.86.
- **Sector:** top-share 0.577 < 0.60 **and** leave-one-sector-out sign holds for *every* sector
  (including `Unknown`, t 2.46). Sector-neutral composite also positive (t 2.66).
- **Liquidity:** high-liquidity subset positive (t 2.40).

### Cost diagnostic — the 10-C thesis, confirmed

| signal | monthly event turnover | quarterly turnover | net-25bps monthly (21d) | net-25bps quarterly (63d) |
|---|---|---|---|---|
| composite_raw | 0.993 | **0.599** | **−0.0008** | **+0.0065** |

The 21d net-of-cost failure was **driven by the event-panel name-rotation structure, not the signal** —
at the realistic quarterly rebalance cadence the edge survives cleanly.

### Honest caveats (this is paper-*review*-ready, not a blowout)

1. **Boundary pass.** IC t = 3.07 is right at the 3.0 bar, not a large margin.
2. **Accruals is the dominant leg.** The composite's IC t (3.07) ≈ `operating_accruals`-alone (3.07);
   `operating_accruals` alone would also clear 63d. The composite's *genuine* added value is
   (a) **best net-of-50bps** of the three and (b) **diversifying fcf's OOS instability** —
   `fcf_to_assets` alone has an OOS positive-window fraction of only **0.375** (it would *fail* the OOS
   bar alone); the composite raises that to 0.625.
3. **Sector-mapping gap.** 57.7% of the long book is unmapped **"Unknown"-sector** names (a
   Norgate/EODHD coverage gap). The *edge* does not depend on them (leave-`Unknown`-out t 2.46) and the
   sector-neutral composite holds (t 2.66) — but a real paper book should trade the **sector-neutral**
   version and the mapping should be improved.
4. **Modest economics.** ~0.65% net quintile long-short spread per quarter at 25bps (~0.35% at 50bps);
   quarterly turnover 0.60 is non-trivial.
5. Old-cohort (t 1.51) and post-2020 (t 1.86) are moderate, though both positive.

### Per-end-report fields

- **Final decision:** `QUARTERLY_QUALITY_COMPOSITE_CONFIRMED_READY_FOR_PAPER_REVIEW`.
- **Quarterly composite survived?** **Yes** — clears every strict gate at 63d.
- **63d IC and t-stat:** IC 0.0454, **t 3.074**.
- **63d net result after 25bps / 50bps (quarterly):** **+0.0065 / +0.0035** (both positive).
- **Turnover at quarterly cadence:** **0.599** (vs ~0.99 monthly event-panel).
- **OOS result:** pooled OOS IC +0.0364, 62.5% of walk-forward windows positive.
- **Cohort stability:** both positive (old t 1.51 / new t 2.17; new stronger — no decay).
- **Sector stability:** top-share 0.577 < 0.60; leave-one-out sign holds for all sectors; sector-neutral
  composite + (t 2.66). Caveat: long-book count is `Unknown`-sector-heavy (mapping gap).
- **Liquidity stability:** high-liquidity subset positive (t 2.40).
- **Ready for Paper Trader review?** **Yes** — first candidate to reach the human paper-review gate (no
  orders, no automation; trade the sector-neutral version).
- **Exact next command:** `review research/output/phase10d_quarterly_quality_composite_validation/paper_review_candidate_package.csv`.
- **Targeted tests:** **16 passed**, 0 failed.
- **Commit recommendation:** **Do not commit** (standing rule). Runner, 16-test suite, doc, and 19
  metadata-only artifacts are on disk for review.

### Recommended Phase 10-E

Build a **paper-only review harness** for the quarterly **sector-neutral** quality composite (quarterly
rebalance; sector-neutral decile/quintile book; cost budget < 25bps) behind an explicit human
approve/reject gate — **no orders, no automation, no deploy**. In parallel, improve the
Norgate/EODHD **sector-mapping coverage** for the `Unknown` bucket (owned data) to firm up the
sector-neutral book. No new data purchase.
