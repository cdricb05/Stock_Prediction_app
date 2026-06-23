# Phase 7-J — Broad Universe Fundamentals Collection and Signal Retest (v1)

**Status:** research / signal-evaluation exercise only.
**Recommendation:** `BROAD_UNIVERSE_SIGNAL_WEAK`.
**Did the signal survive on the broader 296-name universe? → NO.**

**Not** a trading system, production model, order/execution automation, factor-weight
optimization, factor-sign flipping, regime-throttle activation, Paper Trader / GCP /
deployment / broker work. The only live data is free SEC EDGAR public JSON (companyfacts +
submissions) via the stdlib `urllib` SEC client; no paid API, no API key, no package
installed. Large raw payloads and the normalized broad fundamentals panel live only under
`D:\Stock_Prediction_app_data\phase7j_broad_universe_signal_retest\`; the repo received only
committed-safe summaries. Nothing committed or pushed.

Governed by `docs/project_charter_sp500_multifactor_ranking_v1.md`.

---

## The one question this phase answers

Phase 7-I collected free daily prices for **296 usable tickers** (≥ 250 threshold),
unblocking a broad-universe retest. The remaining blocker was broad-universe **SEC
fundamentals** for those names. This phase collects them and runs the retest through the
**unmodified Phase 7-B harness**, reusing the Phase 7-G de-cumulation and the Phase 7-H /
7-F factor construction **exactly**, on the broad cross-section.

> **Did the signal survive on the broader 296-name universe? — NO.**
> The broad final composite scored **mean rank IC = +0.011255 (t = 0.79)** — positive but
> **insignificant**, and it **underperformed the broad price-only momentum baseline**
> (+0.016942), incremental **−0.005687**. By the strict rule it is `WEAK`: positive IC but
> it misses the t-stat bar (≥ 1.5) and fails to beat the price-only baseline by ≥ +0.005.
> Borderline results were **not** rounded up.

---

## Headline comparison (broad 296-name universe)

| Series | mean rank IC | IC t-stat | n_periods | incremental vs price-only |
|---|---:|---:|---:|---:|
| **broad price-only baseline** (12-1 momentum) | **+0.016942** | 0.878 | 107 | 0.0 |
| broad momentum-only (momentum bucket) | +0.012768 | 0.689 | 113 | −0.004174 |
| **broad final composite** | **+0.011255** | **0.793** | 120 | **−0.005687** |
| Phase 7-F 128-name result (**context only**) | +0.017726 | 1.245 | — | — |

The Phase 7-F number is shown **for context only** — it is a different (narrower) universe,
not the primary statistical comparison. The primary comparison is the broad final composite
vs the broad price-only baseline, on the same 296-name cross-section.

### Bucket attribution (broad)

| Bucket | members | mean rank IC | IC t-stat |
|---|---|---:|---:|
| momentum | mom_12_1, mom_6_1, mom_riskadj | +0.012768 | 0.689 |
| value_ttm | sales / earnings / OCF / FCF yield | **+0.014278** | 0.932 |
| quality_ttm | net / operating / OCF margin | **−0.020959** | **−1.471** |
| growth_ttm | revenue / NI / OCF growth | +0.003182 | 0.322 |

The result echoes Phase 7-H on a broader cross-section: **value carries signal, quality
drags hard, growth is flat.** Equal-weighting the four buckets pulls the composite below
plain momentum. On the broad universe the single best alpha is simply **12-1 momentum**;
adding the equal-weight fundamental buckets is net-negative (the composite IC sits below the
price-only baseline). This is the disciplined outcome — we did **not** drop quality/growth
or up-weight value to manufacture a pass (that would be factor selection / weight
optimization on the outcome).

---

## What was collected (the broad fundamentals build worked)

| Metric | Value |
|---|---:|
| Phase 7-I usable price tickers | 296 |
| Tickers mapped to a CIK | 296 |
| SEC companyfacts/submissions cached (this universe) | 288 |
| Normalized SEC fundamental rows | 158,701 |
| Tickers with ≥ 1 SEC fundamental | 224 |
| **Tickers with usable TTM fundamentals (≥ 1 computable bucket)** | **221** (≥ 200 ✓) |
| Non-benchmark price names in the factor panel | 296 |
| Monthly forward-return periods | 120 |

The collection reused the Phase 3-F SEC client (host-restricted to `sec.gov`, throttled,
cache-first) and the Phase 3-F companyfacts normalizer, with the prototype caps raised so
**full** fiscal history is kept (the prototype's 8-period / 24-fact caps were for a tiny
Git-friendly sample). 576 cache hits, **0 network requests** on the recorded run (cache-first
after the initial pilot fetch). TTM levels were de-cumulated from YTD 10-Q flows using the
Phase 7-G logic **unchanged** (clean 3-month frames direct; Q1 == YTD; Q2/Q3 = YTD
differences; Q4 = annual 10-K − Q3 YTD; availability = max of the four legs).

Per-field TTM coverage (broad): operating_cash_flow 186, eps_diluted 178, net_income 176,
capital_expenditures 161, free_cash_flow 159, operating_income 148, revenue 132. Revenue is
the sparsest core field — many post-ASC-606 filers report revenue under
`RevenueFromContractWithCustomerExcludingAssessedTax`, which the reused 3-F concept map
(`Revenues` / `SalesRevenueNet`) does not capture; this thins the value/quality/growth
buckets but still clears the 200-ticker coverage bar.

---

## Gate matrix (`broad_signal_gate_matrix.csv`)

**22 PASS / 2 FAIL / 2 WARN — 0 safety failures.**

* **FAIL (results, honest):** `composite_t_stat_gate` (0.79 < 1.5),
  `beats_price_only_baseline` (incremental −0.0057 < +0.005). `composite_ic_positive`
  **passes** (IC > 0), which is exactly why the verdict is `WEAK` and not `FAILED`.
* **WARN:** `sector_neutralization_caveat` (no point-in-time sectors for the broad
  universe — standardization is **not** sector-neutral here), `survivorship_caveat` (the
  pilot is the current-as-of largest names, not point-in-time membership).
* **PASS:** the price-coverage precondition, SEC collection, the 200-ticker fundamental
  coverage bar, ≥ 2 alpha buckets, composite gradable, strictly-forward labels, placebo
  collapse, and all safety gates (free SEC data only, no paid API, no packages, no weight
  optimization, no sign flipping, no regime selection/weighting, low-vol excluded from the
  composite, no future fundamentals, large data on D: only, repo summaries only, no
  trading/order/automation, no Paper Trader/GCP/broker, not committed, not pushed).

---

## Why `BROAD_UNIVERSE_SIGNAL_WEAK` (not survived, not failed, not blocked)

Strict decision rule (borderline never rounded up):

* IC > 0 **and** t ≥ 1.5 **and** beats price-only by ≥ +0.005 → `SURVIVED`
* IC > 0 but misses any one bar → **`WEAK`** ← we are here
* IC ≤ 0 → `FAILED`
* coverage too low to test → `BLOCKED`

The composite IC is positive (+0.0113), so it is neither `FAILED` nor `BLOCKED` (221 ≥ 200
tickers have usable TTM fundamentals). But it is statistically insignificant (t = 0.79) and
**worse** than the price-only baseline, so it is `WEAK`. The honest reading: broadening the
universe from ~128 to 296 names did **not** rescue the multifactor composite — on the
broader cross-section, the fundamental buckets do not add reliable alpha over simple price
momentum, and quality is an active drag.

---

## Validation & leakage

* Forward labels resolve strictly after the scoring month (`strictly_forward = true`).
* Within-period label-permutation **placebo collapses** (mean IC −0.00016).
* TTM availability = max of all de-cumulation legs; a level is activated only when strictly
  before the scoring month-end (no future fundamentals).
* Regimes (`broad_regime_diagnostic_scoreboard.csv`, year-by-year composite IC) are reported
  **for diagnostics only** — never used to select or weight factors.
* Quintile spread +0.0038, decile spread +0.0067, composite Sharpe 0.34, max drawdown
  −0.33, net (10 bps) spread +0.0036 at 0.21 mean turnover — all weak, consistent with the
  insignificant IC.

---

## Where data lives

* **Repo (committed-safe summaries only):**
  `research/output/phase7j_broad_universe_signal_retest/` — the ten artifacts below.
* **Large raw / normalized data (D: only):**
  `D:\Stock_Prediction_app_data\phase7j_broad_universe_signal_retest\` — per-ticker raw
  companyfacts + submissions JSON, and the 64 MB normalized `broad_fundamentals.csv`. A
  **new** directory; the existing `phase2k_g` and `phase7i_broad_universe` data is untouched.

## Artifacts (`research/output/phase7j_broad_universe_signal_retest/`)

`phase7j_broad_universe_signal_retest.json`, `broad_universe_data_coverage.csv`,
`broad_fundamental_collection_status.csv`, `broad_factor_catalog.csv`,
`broad_factor_scoreboard.csv`, `broad_bucket_scoreboard.csv`,
`broad_composite_scoreboard.csv`, `broad_regime_diagnostic_scoreboard.csv`,
`broad_signal_gate_matrix.csv`, `phase7k_next_plan.json`.

## Tests

`tests/test_phase7j_broad_universe_signal_retest.py` — 23 tests (recommendation vocabulary,
the four strict decision branches incl. coverage-blocked and the exact t-stat boundary, the
gate matrix safety set, the SEC payload prune helpers keeping full history, Phase 7-I
usable-ticker parsing with benchmark exclusion, the no-network collection gate making zero
calls, the regime-diagnostic-only marking, the CIK map, and a guarded end-to-end that grades
the full broad universe from cache and verifies all ten artifacts). All pass.

## Recommended next phase

Because the signal did **not** survive on the broad universe, the next best path is **not**
more local factor polishing or weight tuning (the composite already underperforms plain
momentum). The binding limit is now the **honesty of the data foundation**: a genuinely
**survivorship-free** universe (delisted-name daily prices + point-in-time index membership
+ point-in-time sector history) and broader, ASC-606-aware fundamental coverage, followed by
a **sector-neutral** re-grade. If that cannot be assembled from free sources, the honest
conclusion is that a reliable multifactor edge is **not demonstrable on free data** for this
universe, and the project should pivot from "find more alpha" to "use momentum as the single
honest signal, sized conservatively." Equal weight, no sign flipping, no optimization,
regimes diagnostic only — throughout.

## Safety contract

Free SEC EDGAR data only · no paid API · no packages installed · no factor-weight
optimization · no factor-sign flipping · no regime selection/weighting · low-volatility
excluded from the composite · no future fundamentals · large data on D: only · repo gets
summaries only · existing D: data never overwritten · no trading/order/automation · no Paper
Trader / GCP / broker / deployment · not committed · not pushed.
