# Phase 8-F — Autonomous External Signal Research Operating System (v1)

**Status: `PROMISING_BUT_UNCONFIRMED`** — the autonomous agent OS ran 3 cycles, validated the
S8E-011 lead in depth (and showed a *fixed pre-declared filter repairs its catastrophic tail*),
built executable connectors + schemas + mock fixtures for all six external-information families,
and is now blocked only on provider keys to test the external-event families. No CONFIRMED
external signal yet; one promising-but-unconfirmed lead carried forward. Nothing committed or
pushed.

---

## The one question

> **Can the agent system find, validate, or prepare a real external-information signal by
> combining external events with ticker-specific sensitivity cohorts?**

**Answer (this phase): PREPARE = yes, fully; VALIDATE = deeper, still unconfirmed; FIND = not yet.**
The OS *prepared* the entire external pipeline (detection → adapters → schemas → fixtures →
dry-run commands), *validated* the macro lead S8E-011 down to its tail cause and showed a
pre-declared filter fixes that tail, but could not *find* a CONFIRMED external signal because (a)
the only locally-testable driver family (macro/cross-asset) still fails the portfolio-beats-SPY
gate, and (b) the genuinely-external families (news, revisions, earnings, options, short interest)
have **no provider key in this environment**, so they are pre-registered and blocked, not tested.

---

## What 8-F is: an operating system, not another one-off script

8-A..8-E were tactical phases that each produced a script and a verdict. 8-F instead builds a
**durable research OS** that future phases read and extend, with persistent state, a backlog, a
task board, a decision log, a model-candidate registry, a promotion log, a rejected-hypothesis
graveyard, and provider-agnostic connectors that are **ready the moment a key is supplied**.

| 8-E (factory) | 8-F (operating system) |
|---|---|
| one campaign, then stop | 3 autonomous cycles that read memory, allocate work, decide next |
| 12 generic agents | 15 named agents with explicit responsibilities + artifacts |
| provider data = a plan | provider data = executable adapter + schema + mock fixture + command |
| S8E-011 = promising | S8E-011 = decomposed to its tail cause + fixed-filter stress |
| no durable state | `agent_research_memory.json` future phases load and update |

Engine reuses the persisted 8-E weekly grid on `D:` (1254 symbols × 855,109 obs, 1993–2026) — **no
Norgate rebuild**, honouring "use local data first."

---

## Part 1 — Agent operating memory (durable state)

`agent_research_memory.json` records: current best leads (S8E-011 + its blocker + whether a fixed
filter helps the tail), rejected families (8-B momentum/value/quality, 8-C broad price/volume,
8-D conditional price/volume, most 8-E macro variants), external-data gaps (all six families),
provider readiness (all False), confirmed/promising/rejected signals, open research questions,
next autonomous actions, and stop conditions. Plus `agent_backlog.csv` (10 work items: S8E-011
validation, news/sentiment, analyst revisions, earnings, options, short interest, transcript,
credentials, external event tests, model-candidate promotion), `agent_task_board.csv` (per-cycle
agent task allocation), `agent_decision_log.csv`, `model_candidate_registry.csv`,
`signal_promotion_log.csv`, and `rejected_hypothesis_graveyard.csv` (carries forward 8-B..8-E
family rejections + this phase's).

## Part 2 — Autonomous cycle manager (3 cycles)

- **Cycle 1** — data-foundation reuses the persisted panel; external-data-agent detects keys
  (none) and builds connectors; sensitivity-signal-agent runs the macro (G) family + pre-registers
  A–F; validation-skeptic runs the S8E-011 fixed-filter stress. Decision: **continue**.
- **Cycle 2** — the six event-agents materialize each external schema + connector + mock fixture;
  validation-skeptic completes the S8E-011 tail decomposition. Decision: **continue**.
- **Cycle 3** — risk-portfolio + model-contribution + director: promote/reject, write the
  model-candidate registry, decide the next cycle. Decision: **stop_provider_required** — the next
  meaningful step (families A–F) needs provider access; the OS is built and ready to collect.

Stop condition hit: *"provider access is required for the next meaningful step."* Not a dead end —
the manager escalates with an exact priority-ordered acquisition plan.

## Part 3 — 15 named agents

quant-research-director, data-foundation-agent, universe-construction-agent, feature-library-agent,
external-data-agent, news-sentiment-agent, analyst-revision-agent, earnings-event-agent,
options-signal-agent, short-interest-agent, transcript-tone-agent, sensitivity-signal-agent,
validation-skeptic-agent, risk-portfolio-agent, model-contribution-agent — each owns its artifact
(`data_foundation_report.csv`, `universe_and_identifier_map.csv`, `feature_lineage.json`,
`external_feature_catalog.csv`, the six `*_event_manifest.csv`, `sensitivity_signal_scoreboard`
[→ `external_event_signal_scoreboard.csv`], `validation_skeptic_report.csv`,
`risk_portfolio_report.csv`, `model_contribution_report.csv`).

## Part 4 — Provider/key detection (no secrets)

12 env vars checked by **membership only — values are never read** (verified by a test asserting a
planted secret never appears in output). Result: **0 of 12 present**. Local config files scanned for
key **names** only. `provider_key_inventory.csv` + `provider_connector_status.csv` record presence
and routing (which key would feed which family) without any value.

## Part 5 — Six external schemas + connectors built (key or not)

For each of news/sentiment, analyst revisions, earnings/surprise/guidance, options IV/skew,
short interest, transcript/tone the OS materialized, under `D:\Stock_Prediction_app_data\`:
- a provider-agnostic **executable adapter** (`external_adapters\<src>_adapter.py`) whose
  `--dry-run` prints the exact bounded plan (≤25 tickers / ≤30 days / ≤500 events) and that, given
  a key, would collect into the normalized schema (no secrets printed);
- the **normalized schema header** file (exact fields from the brief, incl. `point_in_time_available_at`);
- a clearly-labelled **MOCK fixture** (`is_mock=True`) that is *never fed into the signal scoreboard*;
- and a committed-safe `provider_acquisition_commands.ps1` (placeholder `<YOUR_KEY>`, no secrets).

This is the "do not stop at a provider plan" requirement satisfied: the pipeline is executable and
one key away from live, bounded collection.

## Part 6 — S8E-011 deep-dive validation (the centerpiece)

S8E-011 = rates sell-off shock (TLT 20d z ≤ −1) × short-duration (rates-negative) sensitivity
cohort × relative-strength confirmation, 20-day horizon. **11,881 events** (6,320 since 2015),
matched-control lift **+0.40%**, EV after 25 bps **+0.13%**, hit-rate 52.3%. It misses CONFIRMED on
two gates: **portfolio does not beat SPY active**, and **worst-decile −15.8% is catastrophic**.

**Tail cause (ex-ante structural, `s8e011_tail_risk_decomposition.csv`):**

| tail slice | share of worst-decile events |
|---|---|
| beta_bucket = 2 (high beta) | **70.2%** |
| vol_bucket = 4 (highest own-vol) | **59.5%** |
| Energy sector | 24.0% |
| liquidity_bucket = 4 | 23.5% |
| year 2009 | 13.0% |

The tail is overwhelmingly **high-beta, high-volatility names** — identifiable *before* the event.

**Fixed pre-declared filter stress (`s8e011_fixed_filter_stress.csv`, no tuning, ex-ante buckets only):**

| filter | n | lift | EV@25bps | worst-decile | beats SPY active | tail improved |
|---|---|---|---|---|---|---|
| baseline (no filter) | 11,881 | +0.0040 | +0.0013 | −0.158 | False | — |
| remove bottom-liquidity quintile | 9,944 | +0.0057 | +0.0012 | −0.156 | False | yes |
| **remove extreme beta tails** | 2,687 | +0.0046 | **+0.0068** | **−0.118** | False | **yes (clears −0.12 floor)** |
| remove top-volatility quintile | 7,266 | +0.0066 | +0.0025 | −0.124 | False | yes |
| remove bottom-liq + top-vol | 6,016 | +0.0061 | +0.0018 | −0.125 | False | yes |

**Do fixed filters help? Yes.** Removing the extreme-beta names lifts the worst decile from −15.8%
to **−11.8% (clears the −12% catastrophic floor)** and raises EV-after-cost **~5×**; the
top-volatility filter gives the strongest lift (+0.66%) and recent lift (+0.68%). **But every
filtered variant still fails portfolio-beats-SPY-active**, so S8E-011 stays
**PROMISING_BUT_UNCONFIRMED**: a real, tail-repairable edge that is not yet a tradeable portfolio
on its own. The natural unlock is *external confirmation* (family F), which needs provider data.
`s8e011_deep_dive.csv` additionally decomposes by sector/year/rate-regime/liquidity/vol/beta and
active-vs-delisted.

## Part 7 — External event × sensitivity tests (A–G)

| family | testable now? | outcome |
|---|---|---|
| A news/sentiment × cohort | no (no key) | NEEDS_PROVIDER — adapter+schema+fixture+command built |
| B analyst revision × cohort | no (no key) | NEEDS_PROVIDER — built (top priority next) |
| C earnings surprise × cohort | no (no key) | NEEDS_PROVIDER — built |
| D options IV/skew × cohort | no (no key) | NEEDS_PROVIDER — built |
| E short-interest × cohort | no (no key) | NEEDS_PROVIDER — built |
| F S8E-011 + external confirmation | no (no external events) | NEEDS_PROVIDER — built (the unlock for the lead) |
| **G macro/cross-asset × cohort** | **yes** | tested: **1 promising-unconfirmed (S8E-011)**, 24 rejected |

Matched controls are cohort-aware (same date/sector/liquidity/volatility/beta bucket, NOT
triggered, NOT in the setup's cohort). Reported per setup: triggered/recent counts, mean/median
forward excess, hit-rate, payoff, EV after 25/50 bps, lift vs matched controls, 2015–2026
validation, walk-forward, worst-decile, concentration, placebo + leakage checks, multiple-testing
deflation (`external_event_signal_scoreboard.csv`, `matched_control_report.csv`).

## Part 8 — Promotion outcomes

No setup meets **CONFIRMED_EXTERNAL_SENSITIVITY_SIGNAL** (S8E-011 fails the tail + portfolio
gates). S8E-011 → **PROMISING_BUT_UNCONFIRMED**. Six external setups → **NEEDS_PROVIDER_DATA**
(adapters/schemas built). `model_candidate_registry.csv`: S8E-011 = `HOLD_BLOCKED_ON_TAIL`;
external families = `BLOCKED_ON_PROVIDER`; **all rows `deployed=False, paper_trader_output=False,
production=False`** (no deployment, no Paper Trader output — by contract).

## Part 9 — Budget

31 of 250 experiments (25 testable + 6 provider), **40% validation/skeptic challenges** (≥30%
required), ≤75/family, all pre-registered before scoring, all failures logged
(`multiple_testing_report.csv`: deflated lift hurdle reported; cohort-aware control + recency +
walk-forward + fixed-filter stress all required).

---

## Decision & next autonomous phase (8-G)

**`PROMISING_BUT_UNCONFIRMED`.** The OS works, the lead is real and its tail is repairable by a
pre-declared filter, and the remaining upside is gated on external data. `phase8g_next_plan.json`
(thresholds **FIXED**, no tuning):

1. **Acquire analyst-revision data (priority 1)** — set FMP/Finnhub/Intrinio key, run
   `analyst_revision_adapter.py` (bounded), then run **family B** and **family F (S8E-011 + upward
   revision confirmation)** through the identical gate.
2. **Acquire news/sentiment (priority 2; GDELT is free)** — run family A.
3. **Broaden the daily universe (S&P 1500 / Russell 3000, chunked)** and re-run S8E-011 with the
   fixed pre-declared filters to test whether portfolio-beats-SPY clears on more data.
4. Bring earnings (C), options (D), short interest (E) online as providers are added.

---

## End-of-task report

- **Files changed (all new, untracked):** `research/run_phase8f_autonomous_external_signal_os.py`,
  `tests/test_phase8f_autonomous_external_signal_os.py`, this doc, and
  `research/output/phase8f_autonomous_external_signal_os/` (38 artifacts). Adapters/schemas/mock
  fixtures live on `D:` (`external_adapters\`, `external_normalized\`); large panel reused from 8-E
  on `D:`. No tracked file modified.
- **Cycles run:** 3 (continue, continue, stop_provider_required).
- **Agent tasks completed:** all 15 agents executed their cycle tasks (task board + decision log).
- **Provider keys detected:** **No** — 0 of 12 (names/presence only; values never read).
- **Provider adapters created:** 6 (news/sentiment, analyst revision, earnings, options, short
  interest, transcript/tone) + 6 normalized schema files + 6 mock fixtures + acquisition commands.
- **Live external collection ran:** **No** (no key; offline-safe).
- **News/sentiment plugged in or blocked:** **Blocked by missing key/history** — connector, schema,
  mock fixture, and dry-run command are built and ready.
- **Normalized external events produced:** 0 real (6 schema files + mock fixtures only; mock is
  labelled and excluded from the scoreboard).
- **S8E-011 validation result:** PROMISING_BUT_UNCONFIRMED — +0.40% lift, +0.13% EV after 25 bps,
  6,320 recent events; fails portfolio-beats-SPY and worst-decile gates.
- **Do fixed filters help:** **Yes** — removing extreme-beta names lifts worst-decile −0.158 →
  −0.118 (clears the −0.12 floor) and EV-after-cost ~5×; top-volatility filter gives best lift
  (+0.66%). None rescues portfolio-beats-SPY → still unconfirmed.
- **External setups tested:** family G (macro) testable → 1 promising-unconfirmed + 24 rejected;
  families A–F pre-registered → NEEDS_PROVIDER.
- **Confirmed external signals:** none.
- **Promising setups:** 1 (S8E-011).
- **Model-candidate registry updates:** S8E-011 HOLD_BLOCKED_ON_TAIL; A–F BLOCKED_ON_PROVIDER; all
  rows non-deployed, no Paper Trader output.
- **Provider data needed:** yes — priority analyst_revision → news_sentiment → earnings → options →
  short_interest → transcript_tone.
- **Tests pass:** yes — 34/34 (8-F); full phase-8 suite green (no regression).
- **Commit appropriate:** **No** — not committed, not pushed, per instruction.
- **Next autonomous phase:** 8-G — acquire analyst-revision provider data, run families B + F under
  the identical gate; broaden the universe for the S8E-011 filtered re-test.

## Safety contract honored

Research-only; local-first (reused 8-E Norgate grid + FRED); no package install; provider keys
detected but **never printed**; **no live collection**; external data **never faked** (mock
fixtures labelled and excluded); large data on `D:` only (repo = summaries/manifests); no weight
optimization; no factor-sign flipping; no regime activation/throttling; no ML fit; no holdout-tuned
thresholds; fixed filters declared a priori on ex-ante structural buckets; no live trading signals;
no broker/orders/automation; no Paper Trader / GCP / deployment; failed experiments not hidden;
**not committed, not pushed.**

## Artifacts (38)

`phase8f_autonomous_external_signal_os.json`, `agent_research_memory.json`, `agent_backlog.csv`,
`agent_task_board.csv`, `agent_decision_log.csv`, `model_candidate_registry.csv`,
`signal_promotion_log.csv`, `rejected_hypothesis_graveyard.csv`, `data_foundation_report.csv`,
`universe_and_identifier_map.csv`, `feature_lineage.json`, `external_feature_catalog.csv`,
`provider_key_inventory.csv`, `provider_connector_status.csv`, `provider_acquisition_commands.ps1`,
`external_event_schema_catalog.csv`, `external_raw_cache_manifest.csv`,
`external_normalized_event_manifest.csv`, `news_sentiment_event_manifest.csv`,
`analyst_revision_event_manifest.csv`, `earnings_event_manifest.csv`, `options_event_manifest.csv`,
`short_interest_event_manifest.csv`, `transcript_tone_event_manifest.csv`, `s8e011_deep_dive.csv`,
`s8e011_tail_risk_decomposition.csv`, `s8e011_fixed_filter_stress.csv`,
`external_event_signal_scoreboard.csv`, `matched_control_report.csv`,
`promising_external_setups.csv`, `confirmed_external_signals.csv`, `failed_external_setups.csv`,
`validation_skeptic_report.csv`, `risk_portfolio_report.csv`, `multiple_testing_report.csv`,
`model_contribution_report.csv`, `research_director_decision.json`, `phase8g_next_plan.json`.
