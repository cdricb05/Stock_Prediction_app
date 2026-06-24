# Phase 8-C — Autonomous Norgate Research Campaign (v1)

**Status:** complete · research only · not committed, not pushed
**Universe:** Russell 3000 Current & Past (broadest feasible survivorship-aware universe)
**Recommendation:** `SIGNAL_RESEARCH_REJECTED_ON_BROAD_DATA` — on the broad, survivorship-aware
Russell 3000 panel, **no** deterministic cross-sectional family passes the `CONFIRMED_SIGNAL`
gate. The strongest lead (downside-volatility) beats SPY full-sample and in 2 of 3 holdouts, but
**loses to the cap-weighted SPY in the most recent 2015-2026 decade** — and the user's gate makes
a recency failure disqualifying. The autonomous campaign ran, challenged its own leads, and
correctly **refused to promote** a historically-strong anomaly that does not hold up recently.

This is research only. Not Paper Trader, not production, not deployment, not broker/orders/
automation, and it makes no live trade recommendation. Norgate is the only (locally installed)
provider; no packages were installed and no paid/network API was used. Large data lives only on
D: (`research_panels/phase8c_russell3000/`, ~89 MB of CSVs); the repo holds committed-safe
summaries. No optimized weights, no factor-sign flipping after results, no regime
activation/throttling, no fundamentals, and no failed experiment is hidden.

---

## 1. What this phase is

8-A *created* the Norgate engine + 12-agent system; 8-B *operated* a single confirmation loop on
the S&P 500 sample; 8-C runs a **bounded, multi-cycle research campaign** on the **broadest
feasible** universe. The orchestrator (`run_phase8c_autonomous_norgate_research_campaign.py`):

1. **expands the dataset** — the data-foundation + universe-construction agents inspect the
   available Norgate "Current & Past" watchlists and pick the broadest feasible one in a fixed
   preference order (Russell 3000 → S&P Composite 1500 → Russell 1000 → Russell 2000 → S&P 1000 →
   S&P 500 fallback), then build a survivorship-aware monthly panel (point-in-time membership,
   active + delisted, total-return close, dollar volume, GICS sector, first/last dates, delisted
   status);
2. **runs up to 3 autonomous cycles** — each cycle reads prior-cycle results, allocates budget,
   **pre-registers** experiments before scoring, runs them, runs validation/skeptic gates, updates
   the confirmed/weak/rejected/blocked sets, and decides stop/continue;
3. **enforces the budget guardrails** — ≤120 experiments, ≤40 per family, ≥30% challenge, ≥25%
   non-momentum, every experiment logged before scoring;
4. **applies the strict `CONFIRMED_SIGNAL` gate** and the campaign-wide multiple-testing
   deflation, then emits a research-director decision + an 8-D plan.

All deterministic, leakage-safe primitives and the comprehensive out-of-sample evaluator
(holdout sub-periods, rolling windows, cost stress, placebo, risk profile) are **imported
unchanged** from the 8-A/8-B engines, so the judgment machinery is identical.

---

## 2. Dataset expansion — the broad survivorship-aware panel

| | S&P 500 sample (8-A/8-B) | **Russell 3000 (8-C)** |
|---|---:|---:|
| symbols (superset) | 1,363 | **12,266** |
| months | 438 | 438 (1990-01 … 2026-06) |
| active | 662 | **3,746** |
| delisted | 701 | **8,520** |
| survivorship dropout | 49.7% | **69.5%** |
| max simultaneous members | ~500 | **3,081** (median 2,959) |

The Russell 3000 Current & Past universe is the #1 preferred universe and was available locally
(12,266 superset symbols). The build took ~9 minutes and persisted 5 CSVs to D: (close, dollar
volume, membership, metadata, SPY). **8,520 of 12,266 names eventually delisted** — a far more
honest, survivorship-aware book than any current-constituent panel, which would silently drop the
dead names. (`panel_build_report.csv`, `data_quality_report.csv`.)

---

## 3. Campaign budget (guardrails honored)

| budget rule | required | this run |
|---|---|---|
| max experiments (all cycles) | ≤ 120 | **30** |
| max per signal family | ≤ 40 | max 17 (low_volatility) |
| challenge / disprove existing leads | ≥ 30% | **15 (50.0%)** |
| non-momentum allocation | ≥ 25% | **21 (70.0%)** |
| all experiments registered before scoring | yes | yes |

**Cycles:** 1 experiment-running cycle (30 experiments); cycle 2 opened and **immediately stopped**
— with every cycle-1 family rejected, no robustness round was warranted, so the campaign did not
burn budget chasing dead families. (`campaign_cycle_log.csv`.) Allocation: 15 full candidate
families (CONFIRM the 3 carried-in 8-B leads at q5/q10 + a broad scan of momentum, reversal,
trend/breadth, liquidity, sector-relative, market-relative) + 15 validation-skeptic CHALLENGE
experiments (per-holdout beats-SPY + cost stress on the 3 priority leads). Per-agent allocation in
`agent_task_allocation.csv`; agenda + hypotheses in `research_agenda.csv`.

---

## 4. Result — no family passes the `CONFIRMED_SIGNAL` gate on broad data

15 candidate families: **0 CONFIRMED, 0 WEAK, 15 REJECTED.** Net Sharpe is after 25 bps;
benchmark is the cap-weighted SPY; EW is the survivorship-aware equal-weight point-in-time
universe.

| family (q5) | net Sharpe@25 | vs SPY | 1990-2004 | 2005-2014 | **2015-2026 (recent)** | rolling 10y beat-SPY | verdict |
|---|---:|---:|---|---|---|---:|---|
| **downside_vol** | 0.942 | **+0.173** | 1.41 ✓ | 0.67 ✓ | **0.73 < 0.95 ✗** | 0.73 | **REJECTED** |
| **low_vol** | 0.905 | +0.135 | beats ✓ | beats ✓ | **loses ✗** | 0.73 | **REJECTED** |
| vol_adj_mom | 0.830 | +0.060 | beats | — | **loses ✗** | 0.46 | REJECTED |
| trend_score | 0.726 | −0.043 | — | — | loses | 0.42 | REJECTED |
| mom_12_1 | 0.691 | −0.079 | — | — | loses | 0.38 | REJECTED |
| sector_rel_mom | 0.662 | −0.107 | — | — | loses | 0.38 | REJECTED |
| high_liquidity | 0.604 | −0.165 | — | — | loses | 0.15 | REJECTED |
| rel_strength | 0.605 | −0.154 | — | — | loses | 0.39 | REJECTED |
| mom_6_1 | 0.580 | −0.189 | — | — | loses | 0.27 | REJECTED |
| illiquidity | 0.399 | −0.370 | — | — | loses | 0.15 | REJECTED |
| rev_losers | 0.299 | −0.471 | — | — | loses | 0.08 | REJECTED |

**The decisive finding: the low-volatility family is a genuinely strong *full-sample* anomaly that
does not beat the cap-weighted SPY in the most recent decade.** downside_vol q5 clears the +0.15
SPY margin (+0.173), the +0.10 EW margin (+0.355), the rolling-10y beat-SPY ≥60% bar (0.73), and
beats SPY in 1990-2004 (1.41 vs 0.76) and 2005-2014 (0.67 vs 0.57) — but in **2015-2026 it returns
a 0.73 Sharpe versus SPY's exceptional 0.95**, so it fails the recency gate. The user's decision
rule makes a recency failure **disqualifying (REJECTED)**, not merely WEAK, so even this 10-of-12-gate
signal is rejected. Everything else (momentum, reversal, trend, liquidity, sector/market relative)
fails to beat SPY even full-sample on the broad universe. Borderline was never rounded up.

This is the honest broad-data extension of the 8-B finding: the "edges" that looked promising on
the narrow S&P 500 sample are **regime artifacts** (strong pre-2015), not signals that beat the
recent large-cap-dominated benchmark.

---

## 5. Holdout / rolling / cost-stress / risk

- **Holdouts (downside_vol q5, the strongest):** 1990-2004 net Sharpe 1.41 (beats SPY 0.76);
  2005-2014 0.67 (beats 0.57); **2015-2026 0.73 (loses to 0.95).** 2 of 3, missing the most recent.
- **Rolling 10-year windows:** downside_vol/low_vol beat SPY in ~73% of windows (clears the 60%
  bar full-sample) but the failure is concentrated in the post-2015 windows.
- **Cost stress (10/25/50/100 bps):** the low-vol leads stay net-positive across the grid
  (turnover is low, ~one-sided 0.1–0.2), so cost is **not** the binding constraint — the recency
  benchmark is. Momentum/reversal degrade faster. (`validation_skeptic_report.csv`.)
- **Risk (`risk_portfolio_report.csv`):** downside_vol q5 drawdown −0.43 (shallower than SPY's
  ~−0.50), beta < 1, ~600 names/quintile (avg holdings ≫ 30), turnover well under the 50% ceiling;
  a large share of book-weight sits in eventually-delisted names (the survivorship-aware exposure).
  Breadth/capacity stress (cycle 3) was not reached because the campaign stopped after cycle 1.

---

## 6. Multiple testing

Search universe = **18 (8-A) + 13 (8-B) + 15 (8-C candidates) = 46** experiments. The deflated
hurdle is `0.15 × max(1, log10(46)) ≈ 0.249` net-Sharpe-over-SPY, applied **on top of** the
placebo (≥0.25), the 2-of-3-holdouts-including-recent requirement, and the rolling-≥60% bar.
downside_vol's full-sample excess (+0.173) is below the deflated hurdle, so it also fails
multiple-testing — but its **primary** disqualifier is recency. **0 of 46** signals survive.
(`multiple_testing_report.csv`.) Ensemble readiness: **not ready** — 0 confirmed signals; no
optimized weights were computed (forbidden). (`ensemble_readiness_report.csv`.)

---

## 7. Decision & 8-D plan

`SIGNAL_RESEARCH_REJECTED_ON_BROAD_DATA`. No paper-research contract was drafted
(`paper_signal_contract.csv` = `NO_CONFIRMED_SIGNAL`).

**8-D (next), research-director call:** the low-volatility family is the only direction with a
real full-sample edge but it fails *recency*. The director should decide between (a) re-testing
the low-vol leads on an **alternate broad universe** (S&P Composite 1500) and **longer rolling
windows** through the identical gate, to see whether the post-2015 underperformance is universe-
specific or a durable large-cap regime; and (b) concluding that naive equal-weight deterministic
cross-sectional anomalies are not worth further pursuit and pivoting (still research-only).
Deterministic rules only — no momentum re-tuning, no optimized weights, no factor-sign flipping,
no regimes, no fundamentals.

---

## 8. End-report answers

- **Files changed (all new):** `research/run_phase8c_autonomous_norgate_research_campaign.py`,
  `tests/test_phase8c_autonomous_norgate_research_campaign.py`, this doc, and
  `research/output/phase8c_autonomous_norgate_research_campaign/` (22 committed-safe artifacts).
  Large panel data on D: only. No existing tracked file modified.
- **Selected universe / panel:** Russell 3000 Current & Past — 12,266 symbols × 438 months,
  1990-01…2026-06, 3,746 active / 8,520 delisted (69.5% survivorship dropout).
- **Cycles / budget:** 1 experiment-running cycle (cycle 2 stopped early); 30/120 experiments;
  challenge 15 (50%), non-momentum 21 (70%); all pre-registered.
- **Confirmed / weak / rejected:** 0 confirmed, 0 weak, 15 rejected.
- **Holdout / rolling:** strongest lead (downside_vol) beats SPY in 1990-2004 + 2005-2014 and in
  ~73% of rolling 10y windows, but loses SPY in 2015-2026 → REJECTED on recency.
- **Cost stress:** low-vol leads net-positive at 10/25/50/100 bps; recency, not cost, is binding.
- **Risk:** dd −0.43 (shallower than SPY), beta < 1, ~600 names, low turnover, heavy
  eventually-delisted exposure.
- **Multiple testing:** 46-experiment search universe, deflated hurdle ≈0.249; 0 survive.
- **Recommendation:** `SIGNAL_RESEARCH_REJECTED_ON_BROAD_DATA`.
- **Tests:** full suite green (see §10).
- **Commit appropriate:** not done, per instruction.
- **Next phase:** 8-D — director decides: re-test low-vol on alternate broad universe vs. pivot.

---

## 9. Safety / scope contract

Norgate only (local provider) · broadest feasible universe built on D: · no network/paid API ·
no packages installed · large data only on D: · no optimized weights · no factor-sign flipping ·
no regime activation/throttling · no fundamentals · failed experiments not hidden · no
orders/broker/automation · no live trading signals · no Paper Trader / GCP / deployment · not
committed · not pushed.

## 10. Artifacts (22)

`research/output/phase8c_autonomous_norgate_research_campaign/`:
`phase8c_autonomous_norgate_research_campaign.json`, `available_norgate_universes.csv`,
`selected_universe_manifest.csv`, `panel_build_report.csv`, `data_quality_report.csv`,
`campaign_cycle_log.csv`, `research_agenda.csv`, `agent_task_allocation.csv`,
`feature_catalog.csv`, `experiment_registry.csv`, `all_experiments_scoreboard.csv`,
`failed_experiments.csv`, `weak_signals.csv`, `confirmed_signals.csv`, `rejected_signals.csv`,
`validation_skeptic_report.csv`, `risk_portfolio_report.csv`, `multiple_testing_report.csv`,
`ensemble_readiness_report.csv`, `paper_signal_contract.csv`, `research_director_decision.json`,
`phase8d_next_plan.json`.
Code: `research/run_phase8c_autonomous_norgate_research_campaign.py` ·
Tests: `tests/test_phase8c_autonomous_norgate_research_campaign.py`.
