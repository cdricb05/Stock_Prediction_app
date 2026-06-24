# Phase 8-B — Autonomous Research Director Orchestrator (v1)

**Status:** complete · research only · not committed, not pushed
**Question:** do the Phase 8-A approved signals (EXP02 12-1 momentum quintile, EXP11
volatility-adjusted momentum quintile) survive *out-of-sample* confirmation — holdout
sub-periods, rolling windows, cost stress, multiple-testing, and risk gates — on
survivorship-aware Norgate data, and is the autonomous research loop ready to operate?
**Recommendation:** `SIGNALS_WEAK_KEEP_RESEARCH_ONLY` — both 8-A survivors are positive
full-sample but **fail to beat SPY out-of-sample** (they beat the benchmark only in 1990-2004,
not in 2005-2014 or the most recent 2015-2026). The research loop ran autonomously and
correctly *refused* to promote a thin, regime-dependent edge.

This is research only. Not Paper Trader, not production, not deployment, not broker/orders/
automation, and it makes no live trade recommendation. The existing 8-A Norgate panel on D: was
**reused** (no recollection). No package install, no network/paid API, no GCP. Large data stays
on D:; the repo holds committed-safe summaries.

---

## 1. What this phase is

8-A *created* the agent definitions; 8-B *operates* them as a deterministic research loop. The
orchestrator (`run_phase8b_autonomous_research_director.py`):

1. reads project state (8-A decision, scoreboard, approved/failed signals, panel manifest) and
   the 6 machine-readable agent contracts;
2. confirms the agent system (12 subagents + 6 contracts) and the D: panel are present/usable;
3. lets the **quant-research-director** autonomously choose the agenda and allocate a bounded
   experiment budget — **no user micro-instructions**;
4. registers a pre-scoring experiment queue (every experiment has hypothesis / owner / inputs /
   success gate / stop condition);
5. implements each agent's responsibility as an explicit artifact;
6. runs the experiment batch, updates the approved/rejected set, and emits a research-director
   decision + an 8-C next-action plan.

The deterministic, leakage-safe primitives (signal blocks, capped equal-weight long-only
simulation, cost model, placebo, benchmarks, metrics, viability gate) are **imported unchanged**
from the 8-A engine, so the judgment is the identical machinery — 8-B only adds the
out-of-sample / multiple-testing / risk layer on top.

---

## 2. Autonomy budget (guardrails honored)

| budget rule | required | this run |
|---|---|---|
| max experiments | ≤ 30 | **27** |
| challenge / disprove the 8-A survivors | ≥ 30% | **10 (37.0%)** |
| non-momentum orthogonal families | ≥ 20% | **7 (25.9%)** |
| all experiments registered before scoring | yes | yes |

Allocation: **CONFIRM** EXP02/EXP11 (2) · **CHALLENGE** holdout sub-periods + 50/100 bps cost
stress on both survivors (10) · **MOMENTUM_ROBUSTNESS** liquidity-gated / 6-1 / sector-relative
variants (4) · **NON_MOMENTUM** low-volatility, downside-volatility, illiquidity, liquidity,
short-term reversal (7) · **RISK_STRESS** breadth concentration (4). Per-agent allocation is in
`agent_task_allocation.csv`; the agenda + rationale in `research_agenda.csv`.

---

## 3. Confirmation result — the 8-A survivors do NOT hold up out-of-sample

Reused panel: **1363 symbols × 438 months, 1990-01 … 2026-06** (662 active / 701 delisted).
Benchmarks: SPY and the survivorship-aware equal-weight point-in-time universe.

| signal | net Sharpe@25 (full) | vs SPY | 1990-2004 (vs SPY) | 2005-2014 | 2015-2026 (recent) | rolling 10y frac beats SPY | verdict |
|---|---:|---:|---|---|---|---:|---|
| **EXP02** 12-1 mom q5 | 0.799 | +0.029 | **1.088 (beats, 0.76)** | 0.445 (loses, 0.57) | 0.780 (loses, 0.95) | 0.38 | **WEAK** |
| **EXP11** vol-adj mom q5 | 0.839 | +0.069 | **1.169 (beats, 0.76)** | 0.464 (loses, 0.57) | 0.796 (loses, 0.95) | 0.46 | **WEAK** |

Both signals are **net-positive in all sub-periods** (trivial for a long-only equity book) but
**beat SPY in only 1 of 3 holdout sub-periods** — the 1990s — and **underperform SPY in the most
recent decade**. Across rolling 10-year windows they beat SPY in only 38% / 46% of windows. They
survive 50 and 100 bps in absolute terms but stop beating SPY at 50 bps. EXP02 and EXP11 are
**0.98 correlated** (net returns) — effectively the same bet.

This is the honest correction to 8-A: the "edge" was real machinery on clean data but is a
**regime artifact** (1990s momentum), not a persistent, benchmark-beating signal. The
confirmation gate — *beat SPY in ≥2/3 holdout sub-periods including the most recent, plus net
positive at 50 bps and a clean risk gate* — was specified to reject exactly this pattern, and it
did. Borderline was never rounded up.

### Candidate scan (13 pre-registered candidate signals)
**0 APPROVED, 7 WEAK, 6 REJECTED.** No candidate cleared the out-of-sample gate this round. The
6 rejected (6-1 momentum, sector-relative momentum, both reversal variants, illiquidity, and
high-liquidity quintiles) failed even the full-sample SPY bar. The most promising **WEAK** leads
— positive, beating SPY full-sample with shallower drawdowns, but not yet out-of-sample
confirmed — are **downside-volatility** (net Sharpe 0.865, +0.095 vs SPY, dd −0.42) and
**low-volatility** (0.856, +0.086, dd −0.39). These are the priority leads for 8-C.

---

## 4. Risk-portfolio findings

| signal | net dd@25 | 1-sided turnover | beta to SPY | top sector | avg weight in eventually-delisted names |
|---|---:|---:|---:|---|---:|
| EXP02 | −0.523 | 0.247 | 0.95 | Information Technology 15.4% | **32.9%** |
| EXP11 | −0.521 | 0.246 | 0.86 | Financials 14.6% | **30.0%** |

- **Drawdowns are deep and honest** (−0.52) now that names that actually died are included.
- The 10% position cap **never binds** (≈100 names ≈ 1% each) → low single-name concentration
  risk; true capacity is ADV-based and is deferred to 8-C.
- **Breadth-concentration stress** (real lever, not the dead position cap): concentrating into
  ~25 names *hurts* (EXP11 net Sharpe 0.839 → 0.650, dd → −0.56; EXP02 → 0.711, dd → −0.62) while
  broadening to ~167 names slightly helps. The thin edge is a diversified, breadth-dependent
  effect — not a concentrated bet.
- **~30% of the book sits in names that eventually delisted** (held only while point-in-time
  members) — the survivorship-aware exposure a current-constituent panel would silently drop.

---

## 5. Multiple testing & ensemble readiness

Search universe = **18 (8-A) + 13 (8-B candidates) = 31** experiments. The deflation is not a
single significance number; it is the **out-of-sample stability + recency + cost-robustness**
requirement applied on top of the full-sample gate. Under it, **0 of 31** signals are confirmed.
Ensemble readiness: **not ready** — 0 confirmed signals, and the only two near-misses (EXP02,
EXP11) are 0.98 correlated. **No optimized weights were computed** (forbidden in 8-B).

---

## 6. Decision & 8-C plan

`SIGNALS_WEAK_KEEP_RESEARCH_ONLY`. Keep the 8-A survivors research-only; do **not** promote. No
paper-research signal contract was drafted (`paper_signal_contract.csv` = `NO_CONFIRMED_SIGNAL`).

**8-C (next):**
1. Keep EXP02/EXP11 research-only — they are regime-dependent, not confirmed.
2. Prioritize the non-momentum leads (**downside-volatility, low-volatility**, then vol-adjusted
   momentum) for confirmation on a **wider/longer survivorship-aware panel** (full Norgate
   superset, optionally Russell 3000), through the *same* out-of-sample gate.
3. Deterministic rules only — no re-tuning of momentum, no optimized weights, no factor-sign
   flipping, no regimes/fundamentals.

---

## 7. End-report answers

- **Files changed (all new, untracked):** `research/run_phase8b_autonomous_research_director.py`,
  `tests/test_phase8b_autonomous_research_director.py`, this doc, and
  `research/output/phase8b_autonomous_research_director/` (21 committed-safe artifacts). No
  existing tracked file modified.
- **Orchestrator worked:** yes — read state + contracts, allocated budget autonomously, ran the
  batch, decided. Subagents 12/12, contracts 6/6, panel usable.
- **Experiment budget used:** 27/30 (challenge 10/37%, non-momentum 7/25.9%).
- **Confirmed signals:** none. **Rejected:** 6 candidates; EXP02/EXP11 downgraded to WEAK.
- **Holdout / rolling:** survivors beat SPY only in 1990-2004; lose to SPY in 2005-2014 and
  2015-2026; rolling 10y beats-SPY fraction 0.38 / 0.46.
- **Cost stress:** survive 10/25/50/100 bps in absolute Sharpe; stop beating SPY by 50 bps.
- **Risk:** dd −0.52, turnover ~0.25, beta 0.86–0.95, ~30% delisted-name weight, breadth-
  dependent (concentration hurts), position cap non-binding.
- **Multiple testing:** 31-test search universe; 0 confirmed after the stability deflation.
- **Recommendation:** `SIGNALS_WEAK_KEEP_RESEARCH_ONLY`.
- **Tests:** 42 passed.
- **Commit appropriate:** not done, per instruction (paths are commit-worthy when the owner
  chooses).
- **Next phase:** 8-C — confirm the low-volatility / downside-volatility leads on a wider panel.

---

## 8. Safety / scope contract

Reused existing D: panel (no recollection) · local Norgate provider only · no network/paid API ·
no packages installed · large data only on D: · no optimized weights · no factor-sign flipping ·
no regime activation/throttling · no fundamentals · failed experiments not hidden · no
orders/broker/automation · no Paper Trader / GCP / deployment · not committed · not pushed.

## 9. Artifacts (21)

`research/output/phase8b_autonomous_research_director/`:
`phase8b_autonomous_research_director.json`, `research_agenda.csv`, `agent_task_allocation.csv`,
`data_panel_check.csv`, `universe_check.csv`, `feature_catalog.csv`, `experiment_queue.csv`,
`experiment_registry.csv`, `all_experiments_scoreboard.csv`, `failed_experiments.csv`,
`approved_signals.csv`, `momentum_agent_report.csv`, `reversal_agent_report.csv`,
`trend_breadth_agent_report.csv`, `volatility_liquidity_agent_report.csv`,
`validation_skeptic_report.csv`, `risk_portfolio_report.csv`, `ensemble_readiness_report.csv`,
`paper_signal_contract.csv`, `research_director_decision.json`, `phase8c_next_plan.json`.
Code: `research/run_phase8b_autonomous_research_director.py` ·
Tests: `tests/test_phase8b_autonomous_research_director.py` (42 tests).
