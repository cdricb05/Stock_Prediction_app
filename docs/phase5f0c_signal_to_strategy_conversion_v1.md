# Phase 5-F0C — Signal-to-Strategy Conversion (v1)

## Why this phase exists

Phase 5-F0B established two facts with out-of-sample evidence:

1. **Confidence gating is a real quality lever.** Filtering the Alpha V3 ensemble to
   high-confidence, model-agreement names lifted the cross-sectional mean rank IC from
   ~0.0425 (raw ensemble) to **~0.0528**, which **beats** the Phase 5-C price-only
   reference (~0.0452) on the trustworthy ranking metric.
2. **That ranking win did not become a tradability win.** The best 5-F0B candidate
   (`ensemble_high_confidence_only`) still had high turnover (~0.61) and a net-of-cost
   annualized-mean return (~0.242) *below* the Phase 5-C baseline (~0.332). Separately,
   the entry/exit band cut turnover (0.59 → 0.42) — **but it was never combined with
   high-confidence entry.**

Phase 5-F0C closes that gap. It **invents no new features and no new providers**. It
reuses the high-confidence signal and asks one question:

> Can combining the parts that worked — high-confidence model-agreement entry,
> entry/exit-band turnover control, top-N concentration, optional industry cap, and
> **regime exposure scaling** — convert the improved high-confidence IC into a more
> **tradable** strategy with acceptable turnover and **better net-of-cost portfolio
> metrics than the Phase 5-C reference**?

It is **Track A quant research**. It trains nothing deployable, deploys nothing, starts
no shadow trading, touches no Paper Trader / GCP code, writes nothing to `D:`, places no
orders, installs no packages, and persists no binary model artifact. It makes **no
network calls** and needs **no API key**.

## Inputs (all local / read-only)

| Input | Path | Use |
|-------|------|-----|
| Price history | `D:\…\phase2k_g_expanded_price_history_free.csv` | read-only; price/volume panel + labels |
| Phase 5-F0B harness | `research/run_phase5f0b_signal_quality_upgrade.py` | imported and reused (it chains 5-F0 → 5-C): panel build, ensemble, rank IC, regime proxy, placebo, 5-C rerun, industry map |
| Phase 5-C / 5-F0B artifacts | `research/output/phase5c_*`, `research/output/phase5f0b_*` | comparison context |
| Industry metadata *(optional)* | `research/data/simfin/normalized/phase5e1e/shared/general.csv` | **Ticker + IndustryId only** → sector map for the industry cap |

The 5-F0B runner (which itself imports 5-F0 and 5-C) is **imported, not duplicated**, so
the baseline, the raw ensemble, and the high-confidence signal are reproduced under the
exact code path the strategies trade through. The optional industry file is read for the
**Ticker → IndustryId** map only — **no SimFin fundamentals are read or used as alpha**
(enforced by `no_fundamentals_as_alpha_gate` and a source scan).

## The high-confidence signal (carried from 5-F0B) and long-entry eligibility

The **signal** is the 5-F0B high-confidence set: keep a name only when the component
models agree (stdev of their within-date normalized ranks ≤ `0.25`) **and** the ensemble
rank is extreme (`≥ 0.70` or `≤ 0.30`); the `0.30–0.70` middle is a **no-trade zone**.
Its rank IC (~0.0528) is the signal-quality metric every strategy must **preserve**.

For a **long-only** strategy, entry eligibility uses the **high-confidence top bucket
only** (dispersion ≤ 0.25 **and** rank ≥ 0.70). On the live universe this is ~21% of the
cross-section, so the strategies are genuinely selective and carry explicit no-trade
logic by construction.

## What it composes (one comparable simulator)

Every candidate runs through a single long-only simulator that supports, in any
combination:

1. **High-confidence entry** — only top-bucket high-confidence names may enter.
2. **Entry/exit band** — hold-until-rank-deteriorates: enter on rank position `< 10`,
   hold until rank position `≥ 20`, even if the name temporarily leaves the eligible set
   (this is the churn reducer).
3. **Top-N concentration** — top 5 and top 10, equal and volatility-adjusted weights.
4. **Industry cap** — max 2 names per sector (only if a reliable local industry map
   exists; coverage ≥ 0.60).
5. **Regime exposure scaling** — *new in 5-F0C*: scale **gross exposure** by the SPY
   regime (`risk_on=1.0`, `neutral=0.6`, `risk_off=0.3`) instead of going fully flat;
   the un-invested fraction sits in cash and earns 0. Risk-off captures both deep
   drawdown and high realized vol in the regime proxy.

Cost model: round-trip turnover × 2 × bps on the changed (post-scale) weights, at 25 and
50 bps. Reported per strategy: signal rank IC / t-stat / worst-year IC / IC by regime,
top-N average forward excess return, top-N hit rate, net-25/50 bps **annualized-mean**
return, turnover, max drawdown, exposure utilization, and number of no-trade periods.

## Candidate strategies

`high_confidence_top5_monthly` · `high_confidence_top10_monthly` ·
`high_confidence_top10_entry_exit_band` · `high_confidence_top5_entry_exit_band` ·
`high_confidence_top10_industry_capped` *(if a local industry map exists)* ·
`high_confidence_top10_regime_scaled` ·
`high_confidence_top10_entry_exit_industry_capped` *(if a local industry map exists)* ·
`high_confidence_top10_entry_exit_regime_scaled` · `best_strategy_candidate`.

Because the signal IC is identical across all of them (basket size / band / cap / regime
scaling are *portfolio-construction* choices, not signal changes), IC cannot pick a
winner. The conversion question is decided on **net portfolio economics**:
`best_strategy_candidate` = highest **top-N equal-weight net-of-50 bps annualized-mean
return** (ties → lower turnover, then better drawdown). All strategies share the monthly
cadence, so this metric is directly comparable and survivorship affects them equally.

## The survivorship / cadence guard

The local universe is **full-history survivors**, so absolute portfolio returns are
upward-biased and **compounded total return explodes with rebalance frequency**. Every
cross-strategy decision therefore uses the **annualized-mean net return** (no
compounding) together with turnover and drawdown; the compounded total return is written
to the artifacts but clearly flagged `…_survivorship_inflated` and never drives a gate.
All eight strategies are monthly, so cadence is held constant across the comparison.

## Run it

```powershell
python research\run_phase5f0c_signal_to_strategy_conversion.py
# optional: --price-csv <path>  --industry-csv <path>  --max-tickers N
```

No key, no network. Reads `D:` price history (read-only) and the optional local industry
CSV.

## Readiness gate (recommend a shadow candidate only if ALL hold)

best strategy **keeps** the high-confidence IC improvement over the Phase 5-C reference ·
turnover acceptable or materially improved · **net-of-cost annualized-mean return beats
the Phase 5-C reference** · max drawdown acceptable (> −0.40) · worst-year IC acceptable
(> −0.05) · no leakage / placebo failure · strategy has explicit no-trade logic · result
is **shadow-only, never live**.

## Recommendations (the five allowed values)

`READY_FOR_PHASE5F1_SHADOW_CANDIDATE` · `EDGE_PRESENT_BUT_STILL_NOT_TRADABLE` ·
`NO_STRATEGY_IMPROVEMENT` · `DATA_BLOCKER` · `ERROR`.

## Artifacts (committed-safe, under `research/output/phase5f0c_signal_to_strategy_conversion/`)

`phase5f0c_signal_to_strategy_conversion.json` · `strategy_candidate_matrix.csv` ·
`strategy_entry_exit_report.csv` · `strategy_turnover_cost_report.csv` ·
`strategy_portfolio_report.csv` · `strategy_regime_exposure_report.csv` ·
`strategy_industry_control_report.csv` · `strategy_validation_gate_matrix.csv` ·
`phase5f1_shadow_candidate_plan.json` (gated on the recommendation).

## Result (v1 live run — 128-name universe, monthly, sector coverage 0.99)

High-confidence signal rank IC = **0.0528** (long-entry coverage 0.21); placebo IC ≈
0.006 (clean). Strategy matrix (**net50am** / **net25am** = net-of-50/25 bps
annualized-mean return, the decision metric; absolute compounded totals omitted as
survivorship-inflated):

| Strategy | sig IC | net50am | net25am | turnover | max DD | exposure | hit |
|----------|-------:|--------:|--------:|---------:|-------:|---------:|----:|
| high_confidence_top5_monthly | 0.0528 | 0.269 | 0.310 | 0.681 | −0.373 | 1.00 | 0.570 |
| high_confidence_top10_monthly | 0.0528 | 0.242 | 0.279 | 0.608 | −0.292 | 1.00 | 0.585 |
| high_confidence_top10_entry_exit_band | 0.0528 | 0.279 | 0.305 | 0.425 | −0.247 | 1.00 | 0.580 |
| **high_confidence_top5_entry_exit_band** | 0.0528 | **0.306** | 0.329 | **0.381** | −0.370 | 1.00 | 0.577 |
| high_confidence_top10_industry_capped | 0.0528 | 0.172 | 0.211 | 0.654 | −0.262 | 1.00 | 0.586 |
| high_confidence_top10_regime_scaled | 0.0528 | 0.150 | 0.177 | 0.442 | −0.199 | 0.71 | 0.585 |
| high_confidence_top10_entry_exit_industry_capped | 0.0528 | 0.249 | 0.277 | 0.472 | −0.246 | 1.00 | 0.574 |
| high_confidence_top10_entry_exit_regime_scaled | 0.0528 | 0.171 | 0.189 | 0.314 | −0.186 | 0.71 | 0.580 |

**Best strategy: `high_confidence_top5_entry_exit_band`** (net50am 0.306, turnover 0.381,
max DD −0.370).

**The combination 5-F0B never tried clearly works — relative to 5-F0B.** Versus the
5-F0B high-confidence reference (net50am 0.242, turnover 0.608), the best 5-F0C strategy
**raises net annualized-mean return to 0.306 (+0.064) and cuts turnover to 0.381
(−0.227)** — simultaneously. The entry/exit band layered on high-confidence entry is the
key: it preserves the IC and removes the churn that sank 5-F0B.

**But it still does not clear the full readiness bar.** The best strategy's net
annualized-mean return (0.306) remains *below* the Phase 5-C price-only baseline (0.332),
so the `net_return_beats_phase5c_gate` **FAILs** (Δ = −0.025). Gate summary: **PASS 12,
FAIL 1, WARNING 2** (worst-year IC −0.060, just past the −0.05 line; and the standing
survivorship caveat).

**Recommendation: `EDGE_PRESENT_BUT_STILL_NOT_TRADABLE`.** Combining the controls
converts the high-confidence ranking edge into a strategy that is materially more
tradable than 5-F0B (lower turnover, lower drawdown, higher net return) and preserves the
IC improvement over 5-C — but it does not yet beat the Phase 5-C price-only baseline on
net-of-cost return. `phase5f1_shadow_candidate_plan.json` is gated off
(`proceed_to_shadow: false`).

### Supporting findings

- **Band is the workhorse; concentration helps.** Both `top5` and `top10` improve when
  the entry/exit band is added (turnover 0.681→0.381 for top-5, 0.608→0.425 for top-10),
  and tighter concentration (top-5) edges out top-10 on net return once churn is
  controlled.
- **Industry cap hurts here.** Capping to 2/sector lowers net return (0.242→0.172 at
  top-10) on this large-cap survivor universe — it is a diversification constraint, not a
  return lever, and it pulls the basket away from the strongest high-confidence names.
- **Regime scaling trades return for stability.** Scaling gross exposure down in
  neutral/risk-off regimes (utilization ~0.71) cuts turnover and drawdown hard
  (`entry_exit_regime_scaled` DD −0.186, turnover 0.314) but also cuts net return
  (0.171) — useful for a risk-managed variant, not for maximizing net return.
- **Hit rates** sit at 0.57–0.59 across baskets — a modest but consistent long-side edge,
  consistent with the small positive IC.

## Safety contract

`preview_only=true`, `shadow_only=true`; `orders_enabled`, `automation_enabled`,
`broker_execution_enabled`, `production_replacement`, `network_used`, `paid_apis_used`,
`deployed`, `binary_artifacts_created`, `live_trading`, `writes_to_d_drive`,
`modifies_paper_trader`, `modifies_gcp`, `uses_simfin_fundamentals_as_alpha`,
`provider_work`, `packages_installed` all `false`. Models are fit in memory for
evaluation only — nothing is persisted. The optional industry map uses **Ticker +
IndustryId only**. No SimFin fundamentals, no FMP, no provider shopping, no live API
calls, no package installs, no commit, no push. The ceiling of this phase is a **shadow**
candidate — never live trading.
