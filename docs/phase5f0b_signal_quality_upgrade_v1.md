# Phase 5-F0B — Alpha Signal Quality Upgrade (v1)

## Why this phase exists

Phase 5-F0 built a serious price/volume/regime **Alpha V3** stack and answered its
question honestly: the ensemble produced a real, leakage-clean out-of-sample edge
(mean rank IC ≈ 0.0425, t ≈ 2.26) but it **did not materially beat** the Phase 5-C
price-only reference (≈ 0.0452). Recommendation: `EDGE_PRESENT_BUT_NOT_DEPLOYABLE`.

The strategic diagnosis carried into this phase: **the signal is real but not
high-quality enough**, and the problem is *not* a lack of features. It is weak
selectivity, possible regime dependence, turnover, and insufficient trade / no-trade
gating. So Phase 5-F0B invents **no new features and no new providers**. It reuses
the existing Alpha V3 signal and asks one question with out-of-sample evidence:

> Can confidence gating, horizon selection, model agreement, regime filters,
> sector/industry controls, and turnover controls turn the existing weak/modest
> ranking edge into a **higher-quality, more tradable** signal — enough to justify a
> **shadow** candidate (Phase 5-F1), still without any deployment?

It is **Track A quant research**. It trains nothing deployable, deploys nothing,
starts no shadow trading, touches no Paper Trader / GCP code, writes nothing to `D:`,
places no orders, installs no packages, and persists no binary model artifact. It
makes **no network calls** and needs **no API key**.

## Inputs (all local / read-only)

| Input | Path | Use |
|-------|------|-----|
| Price history | `D:\…\phase2k_g_expanded_price_history_free.csv` | read-only; price/volume panel + labels |
| Phase 5-F0 harness | `research/run_phase5f0_alpha_model_v3_research.py` | imported and reused (features, walk-forward models, ensemble, rank-IC, regime proxy, placebo, 5-C rerun) |
| Phase 5-C / 5-F0 artifacts | `research/output/phase5c_*`, `research/output/phase5f0_alpha_model_v3_research/` | comparison context |
| Industry metadata *(optional)* | `research/data/simfin/normalized/phase5e1e/shared/general.csv` | **Ticker + IndustryId only** → sector map for industry controls |

The Phase 5-F0 runner (which itself imports Phase 5-C) is **imported, not
duplicated**, so the baseline and the raw ensemble are reproduced under the exact
code path the candidates run through. The optional industry file is read for the
**Ticker → IndustryId** map only — **no SimFin fundamentals are read or used as
alpha** (the `no_fundamentals_as_alpha_gate` and a source scan enforce this).

## What it tests

1. **Confidence / model agreement gating** — keep a name only when the component
   models agree (low stdev of their within-date ranks, `dispersion ≤ 0.25`) **and**
   its ensemble rank is extreme (`≥ 0.70` or `≤ 0.30`); the middle is a **no-trade
   zone**.
2. **Horizon selection** — per-horizon ridge signals at 5d / 10d / 20d and a
   **blended** mean-rank signal, all scored against the primary 20-day label.
3. **Regime gating** — trade only in the SPY **risk-on** regime; flat otherwise.
4. **Turnover control** — **weekly vs monthly** rebalance, plus a **hold-until-
   rank-deteriorates** entry/exit band (enter top-10, hold until rank ≥ 20).
5. **Portfolio-aware selection** — long-only top-10 / top-20, equal & vol-adjusted,
   25 / 50 bps cost sensitivity, turnover, max drawdown.
6. **Sector / industry controls** — max names per sector (industry-neutral-ish) when
   a reliable local industry map exists; otherwise explicitly marked unavailable.
7. **Risk filters** — liquidity floor, per-date volatility cap, extreme-overextension
   cap; reported as a diagnostic effect on the base ensemble IC.

## Candidate strategies

`baseline_phase5c_reference` · `alpha_v3_ensemble` · `ensemble_high_confidence_only`
· `ensemble_risk_on_only` · `ensemble_top10_weekly_rebalance` ·
`ensemble_top10_entry_exit_band` · `ensemble_industry_capped_top10` (if a local
industry map is available) · `blended_horizon_signal` · `best_quality_candidate`.

`best_quality_candidate` is selected on the **trustworthy** quality metric — highest
mean **rank IC** (ties broken toward lower turnover, then higher net annualized-mean
return). Absolute compounded return is **excluded from selection**: it is
survivorship-inflated and not comparable across cadences.

## The survivorship / cadence trap (and the metric we use instead)

The local universe is **full-history survivors**, so absolute portfolio returns are
upward-biased — and **compounded total return explodes with rebalance frequency**
(the weekly basket shows an absurd ~855,795% total return). Comparing a weekly total
return to a monthly one is meaningless. Therefore every cross-candidate decision uses
the **top-10 equal-weight net-of-50 bps annualized MEAN return** (no compounding,
cadence-comparable) together with **rank IC**, **decile spread**, and **turnover**.
The compounded total return is still written to the artifacts but is clearly labelled
`…_survivorship_inflated` and never drives a gate.

## Run it

```powershell
python research\run_phase5f0b_signal_quality_upgrade.py
# optional: --price-csv <path>  --industry-csv <path>  --max-tickers N  --no-weekly
```

No key, no network. Reads `D:` price history (read-only) and the optional local
industry CSV.

## Readiness gate (recommend a shadow candidate only if ALL hold)

best candidate improves the **net portfolio metric** (annualized-mean net return)
over the Phase 5-C reference · IC positive & stable (worst-year IC > −0.05) · decile
spread positive after cost · turnover materially improved or acceptable · drawdown
acceptable · no leakage / placebo failure · the signal has **explicit no-trade /
confidence logic** · the result is **shadow-only, never live**.

## Recommendations (the five allowed values)

`READY_FOR_PHASE5F1_SHADOW_CANDIDATE` · `EDGE_PRESENT_BUT_NEEDS_SHADOW_ONLY` ·
`NO_QUALITY_IMPROVEMENT` · `DATA_BLOCKER` · `ERROR`.

## Artifacts (committed-safe, under `research/output/phase5f0b_signal_quality_upgrade/`)

`phase5f0b_signal_quality_upgrade.json` · `signal_quality_candidate_matrix.csv` ·
`signal_quality_horizon_report.csv` · `signal_quality_regime_report.csv` ·
`signal_quality_turnover_cost_report.csv` · `signal_quality_portfolio_report.csv` ·
`signal_quality_industry_control_report.csv` ·
`signal_quality_validation_gate_matrix.csv` ·
`phase5f1_shadow_candidate_plan.json` (gated on the recommendation).

## Result (v1 live run — 128-name universe, monthly + weekly, sector coverage 0.99)

Candidate matrix (top-10 equal-weight; **net50am** = net-of-50 bps annualized mean
return, the decision metric; absolute totals omitted as survivorship-inflated):

| Candidate | cadence | mean rank IC | t-stat | decile | net50am | turnover |
|-----------|---------|-------------:|-------:|-------:|--------:|---------:|
| baseline_phase5c_reference | monthly | **0.0452** | 1.88 | 0.0194 | 0.332 | 0.359 |
| alpha_v3_ensemble | monthly | 0.0425 | 2.26 | 0.0152 | 0.239 | 0.590 |
| **ensemble_high_confidence_only** | monthly | **0.0528** | 2.26 | 0.0150 | 0.242 | 0.608 |
| ensemble_risk_on_only | monthly | 0.0309 | 1.18 | 0.0134 | 0.191 | 0.618 |
| ensemble_top10_weekly_rebalance | weekly | 0.0429 | 4.52¹ | 0.0130 | 1.118¹ | 0.287 |
| ensemble_top10_entry_exit_band | monthly | 0.0425 | 2.26 | 0.0152 | 0.262 | **0.420** |
| ensemble_industry_capped_top10 | monthly | 0.0425 | 2.26 | 0.0152 | 0.174 | 0.634 |
| blended_horizon_signal | monthly | 0.0330 | 1.80 | 0.0113 | 0.186 | 0.664 |

¹ The weekly t-stat and annualized-mean are inflated by frequency × survivorship —
treat as illustrative, not as evidence of a better signal.

**Best quality candidate: `ensemble_high_confidence_only`.** Confidence gating
(model agreement + no-trade zone) **lifts mean rank IC from 0.0425 → 0.0528**, which
**exceeds the Phase 5-C reference (0.0452)** — ΔIC vs 5-C = **+0.0076**, the genuine
quality win in the trustworthy metric. It trades only ~43% of the cross-section
(no-trade zone), and its decile spread and placebo remain clean (placebo IC ≈ 0.006).

**But it does not clear the full readiness bar.** Its net annualized-mean return
(0.242) is *below* the 5-C baseline (0.332), and its turnover (0.608) is not improved
(the no-trade zone concentrates into fewer, higher-conviction — and higher-churn —
names). Gate summary: **PASS 11, FAIL 1, WARNING 3.** The FAIL is the net-portfolio
improvement gate; WARNINGs are worst-year IC (−0.060, just past the −0.05 stability
line), turnover, and the standing survivorship caveat.

**Recommendation: `EDGE_PRESENT_BUT_NEEDS_SHADOW_ONLY`.** Quality controls produce a
real, leakage-clean improvement in cross-sectional **ranking** (confidence gating
beats 5-C on IC), but not yet a clear improvement in net **tradability** over the
Phase 5-C reference. `phase5f1_shadow_candidate_plan.json` is gated off
(`proceed_to_shadow: false`). The honest path is to keep iterating on confidence /
turnover controls — and, before trusting *any* absolute return, resolve survivorship.

### Supporting findings

- **Horizon selection** — standalone ridge IC vs the 20d label: 5d 0.0278, 10d
  0.0223, 20d 0.0381; the 5/10/20 **blend** lands at 0.0330. The 20-day horizon is
  the strongest single signal; blending shorter horizons does not help here.
- **Turnover control works** — the entry/exit **band** cuts turnover from 0.590 →
  **0.420** with identical ranking IC (it only re-shapes the basket), the cleanest
  tradability win; weekly rebalance cuts per-period turnover to 0.287 but its
  absolute returns are not interpretable under survivorship.
- **Regime gating** — risk-on-only trades ~39% of dates; IC drops to 0.0309 (the
  risk-on subset is not where this signal is strongest), so a naive risk-on filter
  does not improve quality on this universe.
- **Industry controls** — sector cap (max 2/sector, coverage 0.99) leaves ranking IC
  unchanged (it is a portfolio constraint) and slightly raises turnover; it is a
  diversification tool, not a signal-quality lever, on this universe.
- **Risk filters** — liquidity floor + volatility cap + extreme-overextension cap are
  reported as a diagnostic; on this large-cap survivor universe they move the base
  ensemble IC only marginally.

## Safety contract

`preview_only=true`, `shadow_only=true`; `orders_enabled`, `automation_enabled`,
`broker_execution_enabled`, `production_replacement`, `network_used`,
`paid_apis_used`, `deployed`, `binary_artifacts_created`, `live_trading`,
`writes_to_d_drive`, `modifies_paper_trader`, `modifies_gcp`,
`uses_simfin_fundamentals_as_alpha`, `provider_work`, `packages_installed` all
`false`. Models are fit in memory for evaluation only — nothing is persisted. The
optional industry map uses **Ticker + IndustryId only**. No SimFin fundamentals, no
FMP, no provider shopping, no live API calls, no package installs, no commit, no push.
The ceiling of this phase is a **shadow** candidate — never live trading.
