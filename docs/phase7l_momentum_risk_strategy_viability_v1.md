# Phase 7-L — Momentum / Risk Strategy Viability Test (v1)

**Status:** complete · research only · not committed, not pushed
**Question:** can simple price/momentum become a viable risk-managed *research* strategy, YES or NO?
**Recommendation:** `MOMENTUM_STRATEGY_VIABLE_FOR_PAPER_RESEARCH` · momentum viable for paper research = **YES**
**…with first-order caveats that gate any next step** (survivorship bias, post-2016 no-recession sample, modest edge over the equal-weight universe).

This is research only. It is **not** Paper Trader, **not** production, **not** a deployment phase, **not**
broker/order automation, and it makes no live trade recommendation. The failed Phase 7-C/7-F/7-J
multifactor composite is **not** a candidate strategy here.

---

## 1. Why this phase exists

| Phase | Result |
|---|---|
| 7-F | best local 128-name multifactor composite: IC **+0.017726**, t **1.245** |
| 7-H | dense TTM fundamentals did **not** improve it: IC +0.013954, t 0.922 → `TTM_SIGNAL_RELIABILITY_WEAK` |
| 7-J | broad 296-name retest: composite IC +0.011255, t 0.793481, incremental vs price-only **−0.005687** → `BROAD_UNIVERSE_SIGNAL_WEAK` (did not survive) |
| 7-K | survivorship-aware free-data foundation **not buildable** → `FREE_DATA_NOT_SUFFICIENT` |

The multifactor composite is not reliably demonstrable on the free-data foundation. The next step is
**not** another alpha-factor phase. The remaining honest signal is simple price/momentum. This phase
tests whether it can support a realistic, risk-managed research strategy after turnover, transaction
costs, drawdowns, position limits, and regime/risk diagnostics.

---

## 2. Method

- **Universes:** the canonical **128-name** universe (price ∩ annual fundamentals ∩ sector, since 7-C)
  and the broad **296-name** Phase 7-I free-price universe. History **2016-01 … 2026-06** (≈113 usable
  monthly forward periods after the momentum lookback).
- **Score variants (4):** 12-1 momentum, 6-1 momentum, risk-adjusted 12-1 momentum, and the
  equal-weight momentum bucket of the three. Reused unchanged from Phase 7-F; **signs a priori, never
  flipped**; cross-sectional z-scores **without** sector-neutralization (no point-in-time sectors).
- **Portfolios:** top-decile and top-quintile, **long-only, equal weight, monthly rebalance, no
  leverage**, explicit **10% max-position cap** (never binds at these universe sizes — confirmed
  `avg_max_weight` 0.083 for 128-name decile). 4 variants × 2 selections × 2 universes = **16 configs**.
- **Costs:** one-way **10 / 25 / 50 bps**, charged on the full traded fraction Σ|Δw| each rebalance (a
  name's entry and its later exit are both paid). The decision is judged **net of 25 bps**.
- **Benchmarks:** SPY buy-and-hold, an equal-weight-universe buy-and-hold, and the Phase 7-J broad
  price-only baseline **IC** (+0.016942, context only — an IC is selection skill, not a portfolio).
- **Regimes (Phase 7-E):** strictly **diagnostic** — no regime-based selection, no regime-based weighting.
- **Metrics:** annualized return proxy (geometric CAGR), annualized vol, Sharpe proxy, max drawdown,
  turnover, average number of names, hit rate, performance by year, performance by regime.

---

## 3. Headline results (net of 25 bps unless noted)

| config | universe | net Sharpe @25bps | gross Sharpe | ann ret (gross) | max DD | turnover (1-sided) | avg names |
|---|---|---:|---:|---:|---:|---:|---:|
| mom_6_1 · top_decile | 296 | **1.437** | 1.524 | 41.2% | −0.198 | 0.361 | 27.8 |
| momentum_bucket · top_decile | 296 | 1.410 | 1.481 | 39.2% | −0.206 | 0.289 | 27.8 |
| mom_12_1 · top_decile | 296 | 1.323 | 1.384 | 37.8% | −0.223 | 0.261 | 27.7 |
| momentum_bucket · top_decile | 128 | 1.149 | 1.239 | 25.8% | −0.206 | 0.298 | 12.0 |
| mom_12_1 · top_decile | 128 | 1.062 | 1.134 | 26.4% | −0.201 | 0.278 | 12.0 |
| **SPY (buy & hold)** | — | **0.984** | — | 15.2% | −0.239 | — | — |
| **equal-weight universe** | 296 | **1.253** | — | 21.9% | −0.235 | ~0 | 283 |

- **Every one of the 16 momentum configs** posts a net Sharpe @25bps between ~0.99 and 1.44 — all beat
  SPY (0.984); the verdict is **robust, not a best-of-16 selection artifact**. **9 of 16** configs
  *independently* clear the full strict viability bar (positive net, beats both benchmarks, drawdown
  and turnover acceptable), including the canonical 12-1 spec and the diversified bucket.
- Momentum survives even 50 bps: the best config is still net Sharpe **1.35** at 50 bps one-way.
- Drawdowns are shallow (−0.17 to −0.24), comparable to or better than SPY's −0.24.

### The honest qualifier
The equal-weight-universe benchmark already returns **21.9%/yr, Sharpe 1.25**. The best momentum config
beats it by only **+0.18 Sharpe**. **Most of the raw return is being long these names, not momentum
selection.** The yearly table shows the survivorship signature plainly: the momentum book posts +78%
(2020), +65% (2024), +71% (2025) on *current* constituents, while truly survivorship-free SPY posts a
normal 16–26%/yr. See §5.

---

## 4. Strict decision rule (borderline never rounded up)

Judged on the **best** config by net Sharpe @25bps, with robustness reported across all 16:

- **VIABLE** ⟺ net Sharpe@25 > 0 **and** net return@25 > 0 **and** drawdown acceptable **and** turnover
  acceptable **and** beats **both** SPY and the equal-weight universe on Sharpe.
- **WEAK** ⟺ raw momentum positive and beats both benchmarks net of cost, **but** drawdown/turnover make
  it unattractive.
- **STOP** ⟺ fails after costs (net Sharpe ≤ 0 or net return ≤ 0) **or** underperforms either benchmark.
- Acceptability bars (a priori): max drawdown ≥ −0.60 **and** not >15pp deeper than SPY; mean one-sided
  turnover ≤ 0.50/month.

Best config (`u296:mom_6_1:top_decile`): net Sharpe@25 **1.437** > 0 ✓; net return 38.3% > 0 ✓; max DD
**−0.198** (floor −0.60, vs SPY −0.239) ✓; turnover **0.361** ≤ 0.50 ✓; beats SPY 0.984 ✓ and
equal-weight universe 1.253 ✓ → **VIABLE**.

---

## 5. Caveats (first-order — these gate the next step)

1. **Survivorship bias (dominant).** Both universes are *current-as-of* S&P 500 constituents. Phase 7-K
   established there is **no free survivorship-aware data foundation**. Long-only momentum on survivors
   during a momentum-friendly bull market is exactly where survivorship bias inflates results most. The
   equal-weight-universe Sharpe of 1.25 (vs SPY's 0.98) is the bias signature; momentum's incremental
   Sharpe over it is a modest **~0.15–0.18**.
2. **Short, benign sample.** Price history starts 2016 — no 2008-style crash. Drawdowns understate tail
   risk; momentum is known to suffer sharp "momentum crashes" in sharp reversals not present here.
3. **No point-in-time sectors.** Sector exposure is reported only (the book concentrates ~36% in
   Information Technology), never neutralized.
4. **Modelling assumptions.** Costs, the 10% cap, and monthly rebalance are assumptions, not a real book.
5. **Regimes are descriptive only.** Momentum is positive across every regime label here (weakest in
   `rates_up`), but this is in-sample description, not a forecast and not a weighting input.

---

## 6. End-report answers

- **Strategy variants tested:** 12-1, 6-1, risk-adjusted 12-1 momentum, equal-weight momentum bucket — on
  top-decile and top-quintile, both universes (16 configs).
- **128-name results:** net Sharpe@25 **1.06–1.31**; ann ret (gross) 19–30%; max DD −0.17 to −0.24;
  turnover 0.22–0.38. All beat SPY; several fall short of the broad equal-weight-universe bar.
- **Broad 296-name results:** net Sharpe@25 **1.23–1.44**; ann ret (gross) 25–41%; max DD −0.20 to −0.24;
  turnover 0.22–0.36. All beat SPY; most beat the equal-weight universe.
- **Cost-adjusted performance:** robust — best config net Sharpe 1.52 (0bps) → 1.49 (10) → 1.44 (25) →
  1.35 (50). Momentum is not killed by realistic costs.
- **Drawdowns:** −0.17 to −0.24, comparable to / better than SPY (−0.24).
- **Turnover:** one-sided 0.22–0.38/month (≈260–460%/yr); below the 0.50 ceiling; 12-1 and the bucket are
  the lowest-turnover, decile the highest.
- **Benchmark comparison:** beats SPY (Sharpe 0.98) across the board; beats the equal-weight universe
  (1.25) for the broad-universe momentum books; the Phase 7-J broad price-only baseline IC (+0.016942) is
  the IC analogue of the mom_12_1 portfolio.
- **Viable for paper research:** **YES** (strict bars cleared, robustly across 9/16 configs), **subject to
  the §5 caveats** — the survivorship-bias impact is the binding open question.
- **If YES, exact next phase:** Phase 7-M — write a paper-**research** strategy specification (rules,
  rebalance cadence, cost/turnover budget, position limits, drawdown-stop policy) **and a
  survivorship-bias impact study**, before any Paper Trader preview. Still no orders, no automation, no
  optimization. Acquiring survivorship-free data would materially raise confidence.
- **Tests pass:** yes — **30 passed**.
- **Commit appropriate:** not done, per instruction. The four isolated Phase 7-L paths are commit-worthy
  when the owner chooses.

---

## 7. Safety / scope contract

Local price panels + repo-local FRED macro CSVs only · no network · no paid API · no packages installed ·
no new data collected · **no factor-weight optimization · no new alpha factors · failed composite
excluded as a candidate · no factor-sign flipping · no regime-based selection or weighting · no
leverage** · nothing written to D: · no orders/broker/automation · no Paper Trader / GCP / deployment ·
not committed · not pushed. Gate matrix: **23 PASS / 0 FAIL / 3 WARN** (the three WARNs are the
survivorship, sector-PIT, and history-span caveats, surfaced deliberately) · `safety_fail = false`.

---

## 8. Artifacts

`research/output/phase7l_momentum_risk_strategy_viability/`

| file | contents |
|---|---|
| `phase7l_momentum_risk_strategy_viability.json` | full report + decision + robustness + safety |
| `momentum_strategy_scoreboard.csv` | per-config return/vol/Sharpe/DD/turnover/names/hit-rate + benchmarks |
| `momentum_cost_sensitivity.csv` | net ann return & Sharpe at 0/10/25/50 bps per config |
| `momentum_turnover_report.csv` | one-sided & traded-fraction turnover, annual proxy |
| `momentum_drawdown_report.csv` | gross & net-25bps max drawdown per config + benchmarks |
| `momentum_yearly_performance.csv` | per-config & benchmark calendar-year gross/net returns |
| `momentum_regime_diagnostics.csv` | per-config returns by regime (diagnostic only) |
| `momentum_risk_exposure_report.csv` | avg names, max weight, effective N, sector exposure (report-only) |
| `momentum_strategy_gate_matrix.csv` | capability / result / caveat / safety gates |
| `phase7m_next_plan.json` | next-phase plan + hard constraints |

Code: `research/run_phase7l_momentum_risk_strategy_viability.py` ·
Tests: `tests/test_phase7l_momentum_risk_strategy_viability.py` (30 tests).
