# Phase 8-J — Autonomous Alpha Research Daemon

**Status:** `PROMISING_ALPHA_SIGNAL_FOUND` · **Stop reason:** `EXPERIMENT_BUDGET_EXHAUSTED`
**Repo:** `C:\Users\binis\Stock_Prediction_app_push` · **Engine:** `research/run_phase8j_autonomous_alpha_research_daemon.py`
**Tests:** 36/36 (8-J) · full phase-8 suite green · **Not committed, not pushed.**

8-I ran a single 5-cycle discovery *program* and produced one report. 8-J turns that program into a
**durable, resumable research daemon** that keeps selecting hypotheses, running pre-registered
experiments on the fixed 8-E gate, updating persistent memory/queues/registries, promoting/rejecting
candidates, and deciding the next research action — until it hits a clear stop condition, with no user
micro-direction. It is a **local research-automation loop**, not Paper Trader, not orders, not a
deployment.

> Same fixed 8-E gate, same matched controls, same promotion ladder, same anti-p-hacking discipline
> as 8-E..8-I. 8-J adds the *loop*, not new thresholds.

---

## What the daemon is

- **Runtime state (durable, on `D:`):** `D:\Stock_Prediction_app_data\autonomous_alpha_daemon\` —
  `daemon_state.json`, `research_memory.json`, `hypothesis_queue.csv`, `experiment_queue.csv`,
  `experiment_results.csv`, `candidate_signal_registry.csv`, `signal_promotion_log.csv`,
  `rejected_hypothesis_graveyard.csv`, `provider_blocker_registry.csv`, `daemon_run_log.csv`,
  `next_action_decision.json` (11 files). The repo only ever receives committed-safe snapshots.
- **Resumable:** the durable registry is the source of truth; on `--resume` the full campaign ledger
  is rebuilt (scalar metrics) so the recommendation/scoreboard reflect the whole campaign even when a
  resume run scores zero new experiments.
- **13 research-agent roles** with a per-cycle `agent_task_board.csv` / `agent_cycle_summary.csv` /
  `agent_decision_log.csv`: director, data-foundation, universe, external-data, hypothesis-generator,
  macro-sensitivity, earnings-catalyst, news-sentiment, analyst-revision, options-short-interest,
  validation-skeptic, risk-tail, model-candidate.

### Autonomous loop (each cycle)
1. **LOAD/REBUILD** durable state. 2. **ACTIVATE** sources (provider keys by name/presence only;
local earnings; no-key SEC EDGAR; GDELT/FINRA probes — honest about history; Norgate macro). Missing
sources are logged as blockers; the loop never stops on one. 3. **GENERATE** the next batch of
NOT-YET-TESTED pre-registered hypotheses from an expanding bank. 4. **RUN** the batch on the fixed
gate. 5. **VALIDATE** (matched control, 5/10/20/60d, recent 2015-2026, walk-forward, cost, tail,
concentration, placebo + leakage, multiple-testing). 6. **PROMOTE** on the unchanged ladder.
7. **DECIDE** the next action and check stop conditions; persist; loop.

### Stop conditions (the only reasons it halts)
`CONFIRMED_ALPHA_SIGNAL_FOUND` · `HARD_PROVIDER_BLOCKER` · `SAFETY_OR_LEAKAGE_BLOCKER` ·
`EXPERIMENT_BUDGET_EXHAUSTED` · `TIME_BUDGET_EXHAUSTED` · `MANUAL_STOP_FILE_DETECTED`
(manual stop file: `D:\Stock_Prediction_app_data\autonomous_alpha_daemon\STOP_DAEMON.txt`).

### Next-action vocabulary (per cycle)
`CONTINUE_LOCAL_RESEARCH` · `EXPAND_NO_KEY_DATA` · `BUILD_BROADER_PANEL` · `REQUIRE_PROVIDER` ·
`PROMOTE_CONFIRMED_SIGNAL` · `REJECT_FAMILY` · `STOP`.

---

## Validation campaign that ran (3 cycles, fixed gate, nothing tuned)

`--max-cycles 3` over the persisted 8-E weekly grid (1,254 symbols × 855,109 obs, 1993-2026). The
pre-registered bank holds **33 hypotheses** (23 real combinations + 10 challenges/placebos →
challenge fraction **0.303**) plus **3 provider-required** blocked-family entries.

| Cycle | Scored (cum.) | Queue left | Confirmed | Promising | Rejected | Next action |
|---|--:|--:|--:|--:|--:|---|
| 1 | 12 | 21 | 0 | 2 | 6 | CONTINUE_LOCAL_RESEARCH |
| 2 | 24 | 9 | 0 | 5 | 11 | CONTINUE_LOCAL_RESEARCH |
| 3 | 33 | 0 | 0 | 7 | 16 | CONTINUE_LOCAL_RESEARCH |

Stop reason `EXPERIMENT_BUDGET_EXHAUSTED` — the pre-registered bank for this campaign drained. Each
cycle carried its own challenges/placebos (the bank is interleaved so no cycle is validated without
controls).

---

## Results — 7 promising leads, 0 confirmed (`promising_alpha_signals.csv`)

| Signal | Family | n | recent n | lift vs ctrl | EV@25bps | recent lift | worst-decile | verdict |
|---|---|--:|--:|--:|--:|--:|--:|---|
| **S8J-RATES-MACRO-20** *(=S8E-011)* | macro (rates×short-dur) | **11,881** | 6,320 | +0.40% | +0.128% | **+0.34%** | −15.8% | **clean / full coverage** — fails only tail/SPY-active → local fixed-filter path |
| **S8J-RATES-EARNCONF-20** *(=F20)* | rates + earnings confirm | 692 | 394 | +0.53% | +0.378% | +0.49% | −15.5% | coverage-limited (needs provider breadth) |
| **S8J-EARN-SECLEAD-20** | earnings × sector leadership | 933 | 411 | +0.32% | +0.269% | +0.22% | −17.3% | coverage-limited; positive recency |
| **S8J-EARN-SECLEAD-10** | earnings × sector leadership (10d) | 933 | 411 | +0.66% | +0.013% | +0.28% | −12.8% | coverage-limited; thin EV after cost |
| **S8J-EARN-HIGHBETA-20** | earnings × high-beta | **1,168** | 399 | +0.36% | +0.416% | −0.09% | −17.4% | clears count gate but **fails recency** |
| **S8J-EARN-VOLSENS-20** | earnings × vol-sensitive | **1,152** | 380 | +0.53% | +0.503% | −0.03% | −17.2% | clears count gate but **fails recency** |
| **S8J-EARN-RATESUP-20** | earnings × macro-supportive | 417 | 181 | +0.01% | +0.473% | +0.16% | −13.7% | coverage-limited; flat raw lift |

**Provider-required (3):** `S8G-A01` news/sentiment, `S8G-D01` options IV/skew, `S8G-E01` short
interest — no local/no-key PIT history; logged in `provider_blocker_registry.csv` with the exact
PowerShell to supply a key (never executed, never printed). **Rejected (16)** recorded in
`rejected_hypothesis_graveyard.csv`. **0 provider keys present** (detected by name/presence only).

The daemon **reproduces 8-I's two anchor leads exactly** (S8E-011 at 11,881 events; F20 at 692) and
adds new earnings × context combinations. None CONFIRMED: S8E-011 fails only the tail / SPY-active
gate; the earnings leads are short on event count or lose the 2015-2026 recency test.

---

## Decision + best current path

`research_director_decision.json`: **`PROMISING_ALPHA_SIGNAL_FOUND`** — there is a clean,
full-coverage promising lead (S8E-011) whose next step is **local** (a fixed structural
beta-tail/volatility filter), so the call is *promising with a local next step*, not provider-limited.
Per-cycle next action stayed `CONTINUE_LOCAL_RESEARCH` while the bank had untested hypotheses.

**Best current path** (`ranked_next_actions.csv`, P = est. probability of real progress):
1. **P=0.60 · LOCAL** — apply the fixed beta-tail / volatility structural filter to the best promising
   macro × sensitivity and earnings-confirmed leads and re-validate the filtered variant's stability.
2. **P=0.50 · PROVIDER (highest ceiling)** — broad multi-ticker earnings + analyst-revision feed
   (FMP/Finnhub/Zacks/EODHD across S&P 500/1500) + chunked weekly grid rebuild on `D:`; re-run the
   rates+earnings and earnings×context families on the fixed gate.
3. **P=0.40 · NO-KEY/FREE** — widen no-key SEC EDGAR filings + FINRA biweekly short-interest history.

---

## How to run the daemon for a longer campaign (Windows PowerShell)

```powershell
# one cycle (validation)
python research/run_phase8j_autonomous_alpha_research_daemon.py --once
# bounded campaign, resuming durable state
python research/run_phase8j_autonomous_alpha_research_daemon.py --max-cycles 3 --resume
# long autonomous campaign (stops on confirmed / time / experiment budget / manual stop)
python research/run_phase8j_autonomous_alpha_research_daemon.py --time-budget-minutes 120 `
    --max-experiments 200 --resume --stop-on-confirmed --heartbeat-seconds 30
# halt before the next cycle
New-Item D:\Stock_Prediction_app_data\autonomous_alpha_daemon\STOP_DAEMON.txt
```
Flags: `--once --max-cycles N --max-experiments N --time-budget-minutes N --resume --dry-run
--activate-live --stop-on-confirmed --stop-on-provider-blocker --heartbeat-seconds N`. `--dry-run`
does no network collection and writes **no** durable state on `D:` (snapshots only).

**Next autonomous run (recommended):**
```powershell
python research/run_phase8j_autonomous_alpha_research_daemon.py --max-cycles 3 --resume --stop-on-confirmed
```

---

## End-of-task report (answers to the required list)

- **Exact files changed (all new, untracked):** `research/run_phase8j_autonomous_alpha_research_daemon.py`,
  `tests/test_phase8j_autonomous_alpha_research_daemon.py`, this doc, and
  `research/output/phase8j_autonomous_alpha_research_daemon/` (26 committed-safe artifacts). No tracked
  file modified; large runtime state lives only under `D:`.
- **Daemon capabilities built:** persistent state (11 files), resumable loop, 6 stop conditions +
  manual stop file, 13 agent roles with task board/summary/decision log, automatic hypothesis +
  experiment queue generation from an expanding pre-registered bank, full validation battery,
  promotion ladder, provider-blocker registry, autonomous next-action selection, all CLI flags
  (`--once/--max-cycles/--max-experiments/--time-budget-minutes/--resume/--dry-run/--activate-live/
  --stop-on-confirmed/--stop-on-provider-blocker/--heartbeat-seconds`).
- **Cycles run in validation:** 3. **Experiments generated:** 33 pre-registered (bank) + 3
  provider-required. **Experiments scored:** 33 on the fixed gate.
- **Confirmed signals:** none. **Promising signals:** 7 (clean=1 S8E-011; provider/coverage-limited=6).
  **Provider-required signals:** 3 (news, options, short interest).
- **Best trade-idea candidates (`best_trade_idea_candidates.csv`, paper/manual-review only):**
  S8E-011 (rank 1, full coverage), then F20 and the earnings × context leads.
- **Ranked next autonomous actions:** (1) local fixed-filter re-validation **P=0.60**; (2) broad
  earnings+revision provider feed + chunked grid rebuild **P=0.50**; (3) no-key/free filings + FINRA
  short-interest overlay **P=0.40**.
- **Tests pass:** 36/36 (8-J); full phase-8 suite green (see regression).
- **Commit appropriate:** **No** — per instruction (do not commit, do not push).
- **Exact command for the next autonomous run:**
  `python research/run_phase8j_autonomous_alpha_research_daemon.py --max-cycles 3 --resume --stop-on-confirmed`.

## Safety contract honored
Local data first; Norgate for price/macro; on-disk caches reused; **no package install**; large
runtime state on `D:` only (repo gets summaries/snapshots/decision artifacts); **no secrets printed**
(keys by name/presence only); point-in-time joins only; **thresholds fixed a priori and not modified
after results**; **no factor-sign flipping** (combinations use only existing real grid columns);
≥30% challenges/placebos (0.303), interleaved so every cycle carries controls; **external data never
faked** (GDELT/FINRA left connector-live / history-missing; revision proxy labelled + capped below
CONFIRMED; mock fixtures excluded); no weight optimization; no regime activation; no ML fit; no Paper
Trader / GCP / deployment / broker / orders / automation; no live trading signals; failed experiments
not hidden (16 rejected + 3 provider-required recorded); **not committed, not pushed.**

## Artifacts (26)
`phase8j_autonomous_alpha_research_daemon.json`, `daemon_state_summary.json`, `daemon_run_log.csv`,
`research_memory_snapshot.json`, `hypothesis_queue_snapshot.csv`, `experiment_queue_snapshot.csv`,
`experiment_results_snapshot.csv`, `candidate_signal_registry_snapshot.csv`,
`signal_promotion_log.csv`, `rejected_hypothesis_graveyard.csv`, `provider_blocker_registry.csv`,
`agent_task_board.csv`, `agent_cycle_summary.csv`, `agent_decision_log.csv`,
`autonomous_signal_scoreboard.csv`, `confirmed_alpha_signals.csv`, `promising_alpha_signals.csv`,
`provider_required_signals.csv`, `rejected_alpha_signals.csv`, `best_trade_idea_candidates.csv`,
`ranked_next_actions.csv`, `validation_skeptic_report.csv`, `multiple_testing_report.csv`,
`model_candidate_registry_update.csv`, `research_director_decision.json`, `phase8k_next_plan.json`.
