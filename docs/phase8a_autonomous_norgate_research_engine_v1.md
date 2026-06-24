# Phase 8-A — Autonomous Norgate Research Engine & Agent-System Bootstrap (v1)

**Status:** complete · research only · not committed, not pushed
**Question:** does a deterministic price/volume signal survive costs on *survivorship-aware*
Norgate data, and is the research engine ready to scale?
**Recommendation:** `NORGATE_RESEARCH_ENGINE_READY` — agents created, Norgate membership +
active/delisted access work, and **2 of 18** pre-registered signals survive every gate on
survivorship-aware data (with a thin, multiple-testing-discounted edge — see §5).

This is research only. It is **not** Paper Trader, **not** production, **not** a deployment
phase, **not** broker/order automation, and it makes no live trade recommendation. Norgate is
the **only** provider (local desktop database); no network/paid API, no package install, no
GCP. Large data lives only on D:; the repo holds committed-safe summaries.

---

## 1. Why this phase exists

| Phase | Result |
|---|---|
| 7-J | broad-universe multifactor composite did NOT survive (incremental IC −0.005687) → `BROAD_UNIVERSE_SIGNAL_WEAK` |
| 7-K | free data could NOT support survivorship-aware validation (delisted free price 1/20, no PIT sectors) → `FREE_DATA_NOT_SUFFICIENT` |
| 7-L | momentum/risk looked viable **only under survivor-biased free data** (best net Sharpe@25 1.437, 9/16 "viable") |

7-L's headline was an artifact of survivorship bias: the equal-weight benchmark of *current*
constituents already ran at Sharpe ~1.25, so momentum's apparent edge was largely "be long
the survivors". Norgate removes that limitation: it exposes the survivorship-aware **S&P 500
Current & Past** membership superset (active + delisted), point-in-time index membership, and
GICS sectors. This phase rebuilds the test on honest data and bootstraps the agent system that
will run all future cycles.

---

## 2. What was built

- **Part A — 12 Claude Code subagents** under `.claude/agents/`: quant-research-director,
  data-foundation, universe-construction, feature-library, momentum-signal, reversal-signal,
  trend-breadth-signal, volatility-liquidity, validation-skeptic, risk-portfolio,
  meta-model-ensemble, signal-publishing. Each file carries mission, when-to-invoke, allowed
  inputs, required outputs, prohibited actions, validation gates, handoff contract,
  failure-reporting, and the no-hallucination / no-hidden-tuning / no-orders rules.
- **Part B — 6 machine-readable contracts** under `research/agents/`: `agent_manifest.json`,
  `agent_contracts.json`, `experiment_registry_schema.json`, `handoff_contracts.json`,
  `validation_gate_schema.json`, `research_director_protocol.json`.
- **Part C — Norgate adapter** (`NorgateAdapter`): introspects the installed API (no invented
  functions), converts recarrays→DataFrames (with a documented fallback), and logs every
  call/failure to `norgate_data_access_report.csv`.
- **Part D — survivorship-aware panel** on D: (see §3).
- **Part E — 18 deterministic, leakage-safe experiments** (see §4).

### Norgate API functions used
`status`, `databases`, `database_symbols`, `watchlist_symbols`, `security_name`,
`first_quoted_date`, `last_quoted_date`, `classification_at_level` (GICS L1) / `classification`,
`price_timeseries` (total-return adjusted, pandas format), `index_constituent_timeseries`
(S&P 500 PIT membership). Databases present include **US Equities** and **US Equities Delisted**.

---

## 3. The survivorship-aware panel (the whole point)

Universe = the **S&P 500 Current & Past** watchlist (1894 symbols, active + delisted). Of these,
**1363** returned usable price history (531 very-old delisted names had no usable series — logged
as `NO_PRICE` in `data_quality_report.csv`). Monthly total-return closes, dollar volume, PIT
membership, and GICS sectors, **1990-01 … 2026-06 (438 months)**.

| survivorship audit | value |
|---|---:|
| symbols total | 1363 |
| active | 662 |
| delisted | 701 |
| ever S&P 500 members | 1298 |
| ever-member **delisted** | 645 |
| **survivorship dropout fraction** | **0.497** |
| median simultaneous members / month | 501 |
| max simultaneous members / month | 508 |

The panel reconstructs the real ~500-name S&P 500 at each month-end. **Nearly half (49.7%) of
all names that were ever members are delisted** — exactly the population the free-data and
current-constituent panels silently dropped. Large data is on
`D:\Stock_Prediction_app_data\research_panels\phase8a_norgate_sample\` (~10 MB); the repo gets
only the manifest + audit summaries.

---

## 4. Experiments (18 pre-registered, deterministic, leakage-safe)

Families: momentum (12-1, 6-1, 3m), reversal (1m losers/winners), volatility-adjusted momentum,
liquidity-gated momentum, relative strength vs SPY, trend/breakout, sector-relative (breadth)
momentum. Long-only top-decile/quintile, equal weight, 10% cap, monthly rebalance, no leverage.
Costs at 10/25/50 bps on the full traded fraction; judged net of 25 bps. Each experiment carries
a permuted-signal **placebo** and a structural **leakage** check.

### Benchmark shift — the honest result
On survivorship-aware data the **equal-weight-universe Sharpe falls to ~0.775** (vs ~0.97 on a
survivor-only set) and **SPY ≈ 0.77**. Beating these is now genuinely hard.

| experiment | net Sharpe@25 | net DD | turnover (1-sided) | vs SPY | vs EW univ | placebo gap | status |
|---|---:|---:|---:|---:|---:|---:|---|
| **EXP11 vol-adj mom · quintile** | **0.839** | −0.521 | 0.246 | +0.069 | +0.064 | +0.373 | **APPROVED** |
| **EXP02 12-1 mom · quintile** | **0.799** | −0.523 | 0.247 | +0.029 | +0.024 | +0.363 | **APPROVED** |
| EXP12 12-1 mom · decile (liquid) | 0.743 | −0.567 | 0.327 | −0.026 | −0.032 | +0.342 | REJECTED |
| EXP01 12-1 mom · decile | 0.737 | −0.567 | 0.296 | −0.033 | −0.038 | +0.318 | REJECTED |
| EXP18 sector-rel mom · quintile | 0.756 | −0.545 | 0.249 | −0.013 | −0.019 | +0.277 | REJECTED |
| EXP07 1m reversal losers · decile | 0.244 | −0.815 | 0.852 | −0.525 | −0.508 | −0.160 | REJECTED |

Full table in `all_experiments_scoreboard.csv`. **2 APPROVED, 0 WEAK, 16 REJECTED.** Short-term
reversal collapses after costs (turnover ~0.85/month) and fails its own placebo — the engine
correctly kills it. All 18 pass the structural leakage check.

---

## 5. Decision & honest caveats

`NORGATE_RESEARCH_ENGINE_READY`: (a) the 12-agent system + 6 contracts exist; (b) Norgate
historical membership and active/delisted access both work; (c) ≥1 signal survives every gate on
survivorship-aware data. Both survivors are **top-quintile** (12-1 momentum and vol-adjusted
momentum), beat SPY *and* the survivorship-aware EW universe, are placebo-clean, and stay within
the drawdown/turnover bars.

**Caveats that gate the next step (per the director protocol):**
1. **The edge is thin.** Net Sharpe ~0.80–0.84 absolute; the margin over SPY/EW universe is only
   **+0.03 to +0.07 Sharpe**. This is a real but small edge — nothing like 7-L's inflated 1.44.
2. **Multiple testing.** 2 winners out of 18 tested. The protocol requires discounting lucky
   winners; promotion needs **out-of-sample / holdout** confirmation before any ensemble.
3. **Drawdowns are deeper and honest** (−0.52) now that names that actually died are included.
4. **No regimes/fundamentals/optimized weights** were used (forbidden in 8A).

The truthful summary: the survivorship-aware result **demotes** the strategy's apparent strength
versus the survivor-biased 7-L, while **validating the engine and data foundation** and leaving a
small, plausibly-real momentum/vol-adjusted-momentum edge worth a rigorous out-of-sample test.

---

## 6. End-report answers

- **Subagents created:** 12 (all under `.claude/agents/`). **Machine-readable contracts:** 6
  (under `research/agents/`).
- **Norgate access:** active **and** delisted databases present; PIT membership retrieved;
  GICS sectors retrieved. **Historical membership status:** OK (median 501 members/month over
  438 months). **Active/delisted status:** OK (662 active / 701 delisted).
- **Sample panel:** 1363 symbols × 438 months, 1990-01 … 2026-06; survivorship dropout 0.497.
- **Experiments:** 18 run. **Approved:** EXP11 (vol-adj momentum, quintile), EXP02 (12-1
  momentum, quintile). **Failed:** 16 (all momentum deciles, all reversal, all trend/breakout,
  relative strength, sector-relative — they don't clear SPY/EW on clean data).
- **Recommendation:** `NORGATE_RESEARCH_ENGINE_READY`.
- **Tests:** 35 passed.
- **Commit appropriate:** not done, per instruction (the new paths are commit-worthy when the
  owner chooses).
- **Next phase (8-B):** scale to the full Norgate superset, run holdout / sub-period stability +
  a multiple-testing correction on the 2 survivors, and hand them to the risk-portfolio-agent —
  before any ensemble or paper-research preview. No orders/automation/optimization.

---

## 7. Safety / scope contract

Local Norgate provider only · no network/paid API · no packages installed · large data only on
D: · no ML fit / no optimized weights · no regime activation/throttling · no fundamentals · no
factor-sign flipping · no orders/broker/automation · no Paper Trader / GCP / deployment · not
committed · not pushed. Every provider call is logged in `norgate_data_access_report.csv`.

## 8. Artifacts

`research/output/phase8a_autonomous_norgate_research_engine/`:
`phase8a_autonomous_norgate_research_engine.json`, `norgate_api_inventory.csv`,
`norgate_data_access_report.csv`, `norgate_sample_panel_manifest.csv`, `survivorship_audit.csv`,
`data_quality_report.csv`, `research_agent_architecture.csv`, `agent_handoff_contract.csv`,
`experiment_registry.csv`, `all_experiments_scoreboard.csv`, `failed_experiments.csv`,
`approved_signals.csv`, `validation_gate_matrix.csv`, `research_director_decision.json`,
`phase8b_next_plan.json`.
Code: `research/run_phase8a_autonomous_norgate_research_engine.py` ·
Tests: `tests/test_phase8a_autonomous_norgate_research_engine.py` (35 tests).
