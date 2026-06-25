# Phase 8-E — Sensitivity-Aware Multi-Input Signal Factory (v1)

**Track A (quant brain) research only. No Paper Trader / GCP / deployment / orders / automation.
Nothing committed or pushed.**

> Status: **PROMISING_SENSITIVITY_SETUPS_NEED_MORE_VALIDATION** (see Results & Decision).

## The one question

> **CAN WE FIND A REPEATABLE SIGNAL BY MODELING EXTERNAL INPUTS AND TICKER-SPECIFIC
> SENSITIVITIES?** — i.e. when an external driver *shocks*, does the cohort of tickers that is
> historically *sensitive* to that driver (estimated from data, never hard-coded) improve its
> forward return distribution vs **matched controls that saw the same shock but are not in the
> sensitive cohort** — net of costs, out of sample, and in 2015-2026?

## Why this phase exists (the redesign)

Phases 8-A..8-D rejected (a) always-on top-quantile factor portfolios and (b) price/volume-only
*conditional* setups, on broad survivorship-aware data. 8-D's decisive finding: the price/volume
conditional edge was too small to survive costs and placebo/challenge setups produced similar or
larger pre-cost lift. The director's conclusion: **market-derived price/volume/volatility/liquidity
features alone are not enough; the next edge must come from external information and
ticker-specific sensitivity mapping.**

| | 8-A..8-D | 8-E |
|---|---|---|
| Unit of signal | one factor across all tickers | **external driver × ticker sensitivity × context × setup** |
| Sensitivity | assumed identical for all names | **estimated per ticker** (leak-safe rolling beta), never assumed |
| Trigger | price/volume state | **driver shock present AND ticker in the sensitive cohort AND price confirms** |
| Control | same date/sector/liquidity/vol, not triggered | **+ same market-beta bucket AND not in the cohort** (isolates sensitivity) |
| Missing data | n/a | reported as `NEEDS_PROVIDER_DATA` with an acquisition plan — never faked |

The core thesis: *a change in oil, rates, credit, the dollar, or volatility should not affect
Boeing and Accenture the same way.* So we estimate which names load on which drivers, then test
whether the loaded cohort actually moves more than its matched peers when the driver shocks.

## Part A/B — External data inventory, gap, and driver catalog

Inventory is a read-only scan of known local paths; the gap report classifies every external-data
family; the driver catalog records each driver's proxy, mechanism, horizon and availability.

**Locally available (no network / no key):**

- **Macro & cross-asset — `LOCAL_READY` via the locally installed Norgate desktop DB.** Every
  proxy the framework needs is live: `SPY` (1993), the 11 SPDR sector ETFs (`XLE/XLK/XLF/XLV/XLI/
  XLU/XLP/XLY/XLB` from 1998, `XLRE` 2015, `XLC` 2018), `TLT`/`IEF` (duration, 2002), `HYG`/`LQD`
  (credit, 2002/2007), `UUP` (USD, 2007), `USO`/`UNG`/`GLD`/`DBC` (commodities, 2004-07),
  **`$VIX` back to 1990**, `EFA`/`EEM`/`IWM`/`SPHB`. This is the key 8-E unlock: the entire
  cross-asset stack is local, so macro × sensitivity is testable *now* with no provider.
- **Macro (FRED CSVs) — `LOCAL_READY` on disk** under `research/input/`: CPI, WTI, broad USD
  index, fed funds, DGS10/DGS2 yields (used for cross-checks; the tradable proxies above drive the
  betas).
- **Fundamentals — `LOCAL_PARTIAL`:** SimFin quarterly statements + SEC EDGAR normalized
  `broad_fundamentals.csv` (~2.1M rows, PIT filing-dated). Not used as a *driver* in 8-E (kept for
  a future fundamentals-sensitivity family) but inventoried.
- **Sector/industry — `LOCAL_READY`:** Norgate GICS sector + the SPDR sector ETFs; a current-as-of
  sector map is present and flagged **NOT point-in-time**.

**Missing locally → `NEEDS_PROVIDER_DATA` (templates pre-registered, never faked):** analyst
estimates/revisions, earnings surprise/guidance (key-gated), options IV/skew, news, sentiment,
transcripts, short interest/borrow. **News/sentiment exist locally: no.** The exact providers,
costs, priorities and minimal point-in-time schemas are emitted in `provider_acquisition_plan.csv`.

## Part C — Ticker sensitivity map

For every ticker we estimate **leak-safe rolling (252-day, min 126) betas** of daily returns to
each driver proxy: `market` (SPY), `oil` (USO), `rates` (TLT, i.e. duration), `credit` (HYG),
`usd` (UUP), `vix` ($VIX), `commodity` (DBC), `size` (IWM), and `sector` (the ticker's own GICS
SPDR ETF). For each ticker × driver the map records: latest rolling beta, full-sample beta and
correlation, rolling-beta stability (std), sign-consistency, exposure direction, a confidence
score (obs × sign-stability × |beta|), and the minimum-observation flag. `sensitivity_quality_
report.csv` aggregates coverage and median quality per driver. **Direction is estimated from the
data, not assumed** — nothing hard-codes "oil up ⇒ X".

## Part D — Sensitivity cohorts / allotments

Each cohort is the **cross-sectional top/bottom quintile of a driver's rolling beta, recomputed at
every decision date** (so membership is time-varying and fully leak-safe). Cohorts:
`high_beta_market_sensitive`, `low_beta_defensive`, `oil_positive`/`oil_negative_sensitive`,
`rates_positive`/`rates_negative_sensitive`, `credit_stress_sensitive`, `dollar_positive`/
`dollar_negative_sensitive`, `volatility_spike_sensitive` (most-negative VIX beta), and
`sector_leadership_sensitive`. `sensitivity_allotments.csv` reports how many names sit in each
cohort at the latest date; `cohort_membership_panel.csv` is a bounded (recent-2yr) long panel of
(date, symbol, cohort).

## Part E — External-driver conditional setups + matched controls

A setup fires only when **(driver shock) AND (ticker in the sensitive cohort) AND (price/volume
confirm)**. Driver shocks are date-level, leak-safe trailing-20d returns / z-scores of each proxy
(e.g. an oil shock `drv_oil_shock_z ≥ 1`, a VIX spike `drv_vix_spike_z ≥ 1`, a bond rally
`drv_rates_shock_z ≥ 1`). **Matched controls = same date (⇒ same shock), same sector × liquidity ×
volatility × market-beta bucket, not triggered, AND not in the cohort** — so the only thing that
differs between event and control is the driver sensitivity. This directly isolates the
sensitivity contribution. Each setup reports trigger count (overall + 2015-2026), mean/median fwd
excess, hit rate, payoff ratio, lift vs matched controls, lift vs base rate, EV after 10/25/50/100
bps round-trip, worst decile, MAE, sector/year/ticker concentration, walk-forward folds, and a
triggered-event portfolio (equal-weight capped, inactivity allowed, net Sharpe vs SPY and cash).

## Part F — Autonomous campaign + the gate

Up to 3 cycles, ≤200 experiments, ≤60/family, ≥30% skeptic challenges (assessed against testable
setups), all pre-registered before scoring, every failed setup logged. **Challenges** are
adversarial controls: *wrong-cohort* (same shock, opposite cohort), *placebo* (shock + confirm but
no cohort — isolates the cohort), and *no-shock* (cohort + confirm but no shock — isolates the
shock). The three provider-blocked families (revision/news/options × sensitivity) are
pre-registered and marked `NEEDS_PROVIDER_DATA`.

A setup is **`CONFIRMED_SENSITIVITY_SIGNAL`** only if it uses an explicit driver **and** a
sensitivity cohort, has ≥1,000 events (≥100 in 2015-2026), positive EV after 25 bps, lift vs the
cohort-aware matched control ≥ the multiple-testing-deflated hurdle, a hit-rate (+3pp) or payoff
improvement, a triggered book that beats SPY and cash on active weeks, non-catastrophic worst
decile, ≥2/3 positive walk-forward folds, positive 2015-2026 lift, and no year/sector/ticker
concentration — borderline is never rounded up. Otherwise `PROMISING_SENSITIVITY_SETUP`,
`REJECTED`, or `NEEDS_PROVIDER_DATA`.

## Results

**Panel.** Norgate **S&P 500 Current & Past** (broadest feasible survivorship-aware daily
universe; broader universes deferred to a dedicated build), **1,254 symbols — 646 active / 608
delisted**, **855,109 weekly observations**, 1993-07-30 → 2026-05-22. **19/19 driver proxies
loaded** from Norgate. 9 sensitivity drivers (+ own-sector) × leak-safe rolling betas; 11 cohorts;
14 driver-shock columns; cohort sizes ≈20% of members each (clean quintiles).

**Sensitivity map is economically coherent — and emerged from the data, not from hard-coded
signs** (median full-sample betas across 1,254 names): market **+0.94**, own-sector **+0.95**,
high-yield credit (HYG) **+1.34**, duration (TLT) **−0.47**, dollar (UUP) **−0.49**, VIX
**−0.11**, size (IWM) **+0.82**; oil/commodity betas are small (+0.19/+0.40) with low confidence —
correct, since only energy/materials names load on them. Confidence is highest where it should be
(credit 0.95, sector 0.77, market 0.69) and low for diffuse drivers (oil 0.06, VIX 0.07).

**Campaign.** 28 setups registered (25 testable macro × sensitivity + 3 pre-registered
`NEEDS_PROVIDER` templates), **10 challenges = 40%** (≥30% on testable setups), ≤60/family, all
pre-registered. 3 cycles ran: cycle 1 surfaced one lead (S8E-011); cycle 2 refinements (tighter
shocks) all **REJECTED** — narrowing the shock cut events and worsened the cost drag; cycle 3
stress challenges **REJECTED**. **0 confirmed, 1 promising, 24 rejected, 3 needs-provider.**

**The one promising setup — rates-rise × short-duration sensitivity (S8E-011).** When bonds sell
off (`drv_rates_shock_z ≤ −1`, i.e. yields rise), the **rates-negative-sensitive cohort**
(bottom-quintile TLT beta) with positive relative strength outperforms over 20d:

| metric | value |
|---|---|
| events (total / 2015-2026) | 11,881 / 6,320 |
| matched-control lift (same shock, **not** in cohort) | **+0.40%** |
| 2015-2026 lift | **+0.34%** |
| EV after 25 bps round trip | **+0.13% (survives cost)** |
| deflated multiple-testing hurdle (25 searches) | 0.0021 — **cleared** (lift 0.0040) |
| why **not** confirmed | triggered book does **not** beat SPY on active weeks; **worst decile is catastrophic** (tail risk) |

This is the first signal in the 8-x series to show a *cost-surviving, recency-positive,
sensitivity-specific* matched-control lift — but it fails the portfolio and tail-risk gates, so it
is `PROMISING`, not `CONFIRMED` (borderline is never rounded up).

**The rest — costs dominate, and placebos confirm the caution.** Most macro × sensitivity setups
showed only a few-to-~40 bps matched-control lift that the 50 bps round trip erases (EV after 25
bps negative): e.g. sector-leadership (S8E-070, 33,723 events, +0.25% lift, EV −0.09% — *just*
under cost), USD-positive (S8E-050, +0.34% lift, +0.74% recent, EV −0.61%), oil-negative on
oil-down (S8E-002, +0.36% lift, EV −1.11%). Discipline held: **wrong-cohort challenges**
(same shock, opposite cohort) show ≤0 lift — the sensitivity *direction* matters; **no-cohort and
no-shock placebos** are all rejected, and multiple-testing flags exactly one placebo (S8E-911:
VIX-spike + uptrend with **no** cohort) as showing raw lift (+0.46%) — a direct caution that part
of the apparent vol-defensive effect is shock/bucket drift, not cohort-specific alpha.

**Provider gap.** News/sentiment are **not** local. Analyst estimates/revisions, earnings
surprise, options IV/skew, news, sentiment, transcripts, and short interest are all
`NEEDS_PROVIDER`; their setup templates are pre-registered and the acquisition plan (providers,
cost, priority, minimal PIT schema) is emitted — priority order **analyst_revision → earnings_
surprise → news_sentiment → options_iv → short_interest**.

## Decision

**Recommendation = `PROMISING_SENSITIVITY_SETUPS_NEED_MORE_VALIDATION`.**

Modelling **external driver shocks × estimated ticker-sensitivity cohorts** changed the answer
from 8-D's flat rejection: one setup (rates-rise × short-duration sensitivity) now clears the
matched-control-lift, cost (EV after 25 bps **+0.13%**), recency, and multiple-testing hurdles
that *every* price/volume conditional setup failed in 8-D. It is **not** confirmed — its triggered
book does not beat SPY on active weeks and carries catastrophic tail risk — so it is promising, not
tradeable. The sensitivity *map* itself is a durable, economically-coherent asset (correct,
estimated betas to credit/rates/USD/VIX/market/sector). The next edge most likely needs richer
external inputs (analyst revisions, options IV) that are not local.

**Phase 8-F (director call), thresholds FIXED (no tuning on these results):**
1. Broaden the daily universe (S&P Composite 1500 / Russell 3000 Current & Past via a dedicated
   chunked build) and re-run the **identical** gate to test whether S8E-011 and the near-miss
   cost-bound setups clear the portfolio/tail gates on more (smaller-cap) names.
2. Acquire the highest-priority provider dataset — **analyst estimate revisions** — and add a
   `revision_event × sensitivity` driver family to the *same* framework (the template and PIT
   schema are already pre-registered in `provider_acquisition_plan.csv`).

This is a research checkpoint only. **Not committed. Not pushed.**

## End-of-task report answers

- **Exact files changed (all new, untracked):** `research/run_phase8e_sensitivity_aware_signal_
  factory.py`, `tests/test_phase8e_sensitivity_aware_signal_factory.py`, this doc, and
  `research/output/phase8e_sensitivity_aware_signal_factory/` (20 artifacts). Large panel on D:
  only. No tracked file modified.
- **Local external data found:** Norgate macro/cross-asset proxies (19/19 live: SPY, 11 sector
  ETFs, TLT/IEF, HYG/LQD, UUP, USO/UNG/GLD/DBC, $VIX, EFA/EEM/IWM/SPHB); FRED CSVs (CPI/WTI/USD/
  fed funds/yields); SimFin + SEC EDGAR fundamentals (LOCAL_PARTIAL); current-as-of sector map.
- **Missing external inputs:** analyst estimates/revisions, earnings surprise, options IV/skew,
  news, sentiment, transcripts, short interest — all `NEEDS_PROVIDER` (acquisition plan emitted).
- **Sensitivity drivers tested:** market, oil, rates (duration), credit, USD, volatility (VIX),
  broad commodity, size, own-sector — 9 + sector, leak-safe rolling 252d betas.
- **Cohorts created:** 11 (high/low beta, oil ±, rates ±, credit-stress, USD ±, vol-spike,
  sector-leadership), per-date quintiles of estimated betas.
- **Setup experiments run:** 28 (25 testable macro × sensitivity + 3 provider templates), 10
  challenges (40%), ≤60/family, 3 cycles.
- **Confirmed sensitivity signals:** none.
- **Promising sensitivity setups:** 1 — S8E-011 (rates-rise × short-duration cohort): +0.40%
  matched-control lift, +0.34% recent, **+0.13% EV after 25 bps**; fails the portfolio-beats-SPY
  and worst-decile gates.
- **Provider data needed:** yes, to extend beyond macro — priority analyst_revision →
  earnings_surprise → news_sentiment → options_iv → short_interest.
- **Whether news/sentiment exists locally:** **no.**
- **Whether tests pass:** yes — 34/34 logic + synthetic-integration; 11 e2e validate the committed
  artifacts (see suite run).
- **Whether commit is appropriate:** **no** — instructed not to commit; research checkpoint only.
- **Exact next phase:** 8-F — broaden the daily universe and/or acquire analyst-revision provider
  data, then re-run the **identical** gate (thresholds fixed).

## Safety contract (all enforced & asserted)

Research only; Norgate desktop DB + on-disk FRED CSVs only; no network/paid API; no package
install; large data only under `D:\Stock_Prediction_app_data\research_panels\phase8e_sensitivity`;
repo gets summaries only. **External data never faked** — missing families become an acquisition
plan. No price/volume-only mining (every candidate uses an external driver + estimated cohort); no
single universal factor; sensitivity direction estimated, not assumed; no weak full-sample
promotion; no weight optimization; no factor-sign flipping after results; no regime
activation/throttling; no ML fitting; no holdout feedback used to tune thresholds; failed
experiments not hidden; no live trading signals; no orders/broker/automation; no Paper Trader /
GCP / deployment; **not committed; not pushed.**

## Artifacts (20, committed-safe summaries)

`phase8e_sensitivity_aware_signal_factory.json`, `local_external_data_inventory.csv`,
`external_data_gap_report.csv`, `external_driver_catalog.csv`,
`ticker_external_sensitivity_map.csv`, `sensitivity_cohort_catalog.csv`,
`sensitivity_quality_report.csv`, `sensitivity_allotments.csv`, `cohort_membership_panel.csv`,
`setup_experiment_registry.csv`, `sensitivity_setup_scoreboard.csv`, `matched_control_report.csv`,
`promising_sensitivity_setups.csv`, `confirmed_sensitivity_signals.csv`,
`failed_sensitivity_setups.csv`, `provider_acquisition_plan.csv`, `validation_skeptic_report.csv`,
`multiple_testing_report.csv`, `research_director_decision.json`, `phase8f_next_plan.json`.

Large panel (D: only, not committed): `weekly_observation_grid.csv`, `symbol_metadata.csv`,
`ticker_sensitivity_map_full.csv`, `spy_daily_close.csv`.
