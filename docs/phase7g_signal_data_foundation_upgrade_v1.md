# Phase 7-G — Signal Data Foundation Upgrade (v1)

**Status:** research / data-quality exercise only.
**Recommendation:** `DATA_FOUNDATION_PARTIAL`.
**Not** a trading system, production model, order/execution automation, factor-weight
optimization, or regime activation. No live or paid data calls. Nothing committed or pushed.
The D: price panel is read-only; nothing was written to D:.

Governed by `docs/project_charter_sp500_multifactor_ranking_v1.md`.

---

## Why this phase exists

Phase 7-F lifted the equal-weight composite mean rank IC from **−0.0074** to **+0.0177**,
but honestly attributed most of the gain (+0.0197) to *excluding* low-volatility as alpha
rather than to better fundamentals (+0.0054), and the composite was not statistically
significant (t ≈ 1.25). Its dominant remaining weaknesses were **data-foundation** problems,
not modelling ones:

* a constrained ~128-name, current-constituent universe;
* survivorship / current-constituent bias;
* a static, non-point-in-time sector map;
* sparse clean quarterly / TTM fundamentals (7-F's TTM graded **0** months).

Phase 7-G asks a single disciplined question: **how much of that foundation can be repaired
using only local, already-collected data — and what must be collected later?**

---

## What was built

`research/run_phase7g_signal_data_foundation_upgrade.py` — an offline inventory + readiness
engine that:

1. Inventories the current universe (ticker count, date range, price/sector/fundamental coverage).
2. Inventories every relevant local data source and flags whether each can expand the universe.
3. Assesses point-in-time sector readiness.
4. **De-cumulates YTD 10-Q flows into point-in-time 3-month quarters** and rolls trailing-4-quarter
   TTM windows, measuring how dense and continuous a quarterly/TTM panel can be built locally.
5. Builds a broader-universe candidate (bounded by local price coverage).
6. Emits go/no-go gates, a recommendation, and a scoped Phase 7-H plan.

### The densification mechanism (the one real local upgrade)

The local SEC artifact carries ~35k 10-Q rows, but most quarterly flow line items are
**cumulative year-to-date**, not 3-month — and the clean 3-month `CYyyyyQn` frames are sparse
(~2 per ticker). 7-F therefore used only clean frames and graded 0 TTM months.

7-G recovers 3-month quarters point-in-time-safely:

* clean `CYyyyyQn` frame → used directly;
* Q1 YTD == Q1 3-month;
* Q2 = Q2_YTD − Q1_YTD, Q3 = Q3_YTD − Q2_YTD (a predecessor must sit one quarter, 80–100 days,
  earlier — keyed by fiscal-period-end spacing, **not** the unreliable `fiscal_year` column);
* Q4 = annual 10-K − Q3_YTD.

Each constructed quarter's availability is the **max** of its constituent legs, so nothing leaks.

| Core flow | clean-frame 3-mo | de-cumulated 3-mo | total 3-mo quarters | TTM windows |
|---|---:|---:|---:|---:|
| revenue | 133 | 810 (+400 Q4) | 1,679 | 1,347 |
| net_income | 227 | 1,290 (+642 Q4) | 2,691 | 2,267 |
| operating_cash_flow | 209 | 1,750 (+875 Q4) | 3,608 | 3,122 |

De-cumulation multiplies usable quarterly flow density ~4–6× over clean frames alone.

---

## Determinations

| Question | Answer | Evidence |
|---|---|---|
| Does a **broader universe** exist locally? | **No** | One price panel, 128 names. SEC directory lists 10,415 tickers but **none** have local price/fundamentals (10,287 addressable-but-uncollected). |
| Do **point-in-time sectors** exist locally? | **No** | Only a static current-as-of map (`point_in_time=false`); no as-of-date sector history. |
| Are **denser quarterly/TTM fundamentals** feasible locally? | **Yes** | De-cumulation yields continuity-ready tickers well above MIN_NAMES (20): revenue 68, net_income 106, operating_cash_flow 116. |
| Is **survivorship bias** addressable locally? | **No** | No delisted-name price history; the universe is current constituents only. |
| Is a **Phase 7-H retest** allowed? | **Yes, scoped** | `fundamental_density_only` — a same-universe fundamental-signal retest, **not** a universe/survivorship robustness retest. |

---

## Gate matrix (`data_foundation_gate_matrix.csv`)

10 PASS / 3 FAIL, **0 safety failures**.

* **FAIL (capability, honest):** `broader_universe_available_locally`,
  `survivorship_bias_addressable_locally`, `point_in_time_sector_available_locally`.
* **PASS (capability):** `quarterly_3mo_densification_feasible`, `ttm_continuity_sufficient`,
  `fundamental_density_materially_upgradable_locally`.
* **PASS (safety):** no live data, no paid API, reproducible/offline, no trading/order/automation,
  no factor-weight optimization, no regime-throttle activation, price panel read-only.

---

## Why `DATA_FOUNDATION_PARTIAL`

A genuine local upgrade exists — a materially denser, point-in-time TTM fundamental panel — so
the phase is not blocked. But the **dominant** 7-F weaknesses (universe breadth, survivorship,
PIT sectors) are **not** locally fixable and require controlled, explicitly-approved future
collection. Hence: partial, not `READY`, and not `NEEDS_CONTROLLED_DATA_COLLECTION`.

A Phase 7-H retest is therefore permitted **only** at `fundamental_density_only` scope: rebuild
the 7-F value/quality/growth buckets on the dense TTM panel and re-grade through the unmodified
7-B harness on the same ~128 names — equal weight, no sign flipping, no optimization, regimes
diagnostic only. This can tell us whether better fundamentals help the signal; it **cannot**
resolve the universe/survivorship problem.

---

## Remaining blockers (→ controlled collection, not this phase)

1. **Universe breadth + survivorship** — acquire survivorship-aware historical index membership
   plus daily prices for names beyond the current 128 (including delisted).
2. **Point-in-time sectors** — acquire historical GICS/sector assignments with effective dates.
3. **Optional fundamental backfill** — only if local de-cumulation proves insufficient downstream.

See `phase7h_next_plan.json` for the scoped immediate (local) and collection-gated steps.

---

## Artifacts (`research/output/phase7g_signal_data_foundation_upgrade/`)

`phase7g_signal_data_foundation_upgrade.json`, `universe_inventory.csv`,
`local_data_source_inventory.csv`, `sector_data_readiness.csv`, `quarterly_ttm_readiness.csv`,
`broader_universe_candidate.csv`, `data_foundation_gate_matrix.csv`, `phase7h_next_plan.json`.

## Tests

`tests/test_phase7g_signal_data_foundation_upgrade.py` — 16 tests (de-cumulation arithmetic,
clean-Q1-as-baseline, non-contiguous-predecessor leakage guard, TTM contiguity, gate/recommendation
logic, and guarded end-to-end determinations over the real local artifacts). All pass.

## Recommended next phase

**Phase 7-H — Signal Retest on Densified TTM Fundamentals** (`fundamental_density_only`), in
parallel with planning a controlled collection effort to remove the universe/survivorship/PIT-sector
blockers that the local data cannot.

## Safety contract

Preview-only · no live/paid data · no trading/order/automation · no factor-weight optimization ·
no factor-sign flipping · regimes diagnostic only · D: read-only · not committed · not pushed.
