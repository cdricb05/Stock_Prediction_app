# Phase 8-L — Autonomous Data-Family Expansion, Provider Acquisition Decision & Signal-Confirmation Factory

**Track A (quant brain) research only. Preview/analysis. No Paper Trader, no GCP, no deployment, no
broker/orders/automation, no live trading signals. No commit, no push.**

## What 8-L answers

> **Which data sources must we activate or subscribe to, and which signal families become testable or
> confirmable when we do?**

8-K became a self-expanding alpha factory that resolves every missing data family to a concrete status.
8-L does **not** consume the 8-K output and stop. It rebuilds the 8-K leads, then runs **12 new waves**
whose work is auditing every missing family, inspecting local caches, attempting free/no-key activation,
detecting provider keys by name, ranking the *smallest provider set that unlocks the most high-value
families*, deciding what to subscribe to, and **continuing to test local signals** (tail-risk repair on
the clean macro leads, earnings-confirmed candidates) while data discovery runs — plus placeholder
experiment **specs** (never fake results) for every provider-gated signal family.

## Reuse (no re-implementation)

`8-L → 8-K → 8-J → 8-I → 8-H → 8-G → 8-F → 8-E`. The entire validated scoring / gate / control /
promotion / validation / report stack is imported from 8-K. 8-L adds only the expansion loop, the
14-family acquisition matrix, the provider-decision artifacts, the unlocked-signal specs, the
tail-repair / provider-expansion scoreboards, and a trade-idea registry with a trade-ready reason.

## Terminal stop conditions (the only reasons it halts)

`CONFIRMED_ALPHA_SIGNAL_FOUND` · `PROVIDER_ONLY_BLOCKER` (with `--stop-on-provider-only`) ·
`TIME_BUDGET_EXHAUSTED` · `EXPERIMENT_BUDGET_EXHAUSTED` · `WAVE_BUDGET_EXHAUSTED` ·
`MANUAL_STOP_FILE_DETECTED` · `SAFETY_OR_LEAKAGE_BLOCKER`.
**An empty hypothesis bank with waves remaining does NOT stop the factory — it refills the next wave.**
Manual stop: drop `STOP_FACTORY.txt` in the state dir.

## The 12 waves

| # | Wave | Scores? | Focus |
|---|------|---------|-------|
| 1 | WAVE_PHASE8K_STATE_REBUILD | ✅ | rebuild S8E-011 anchor, sector-leadership, F20 earnings-confirmed on the fixed gate |
| 2 | WAVE_MISSING_DATA_FAMILY_AUDIT | — | resolve all 14 families to a concrete status |
| 3 | WAVE_LOCAL_CACHE_DISCOVERY | — | inventory local caches (earnings, filings, Norgate, FRED) |
| 4 | WAVE_FREE_NO_KEY_ACTIVATION | — | attempt SEC EDGAR / GDELT / FINRA free sources, record outcomes |
| 5 | WAVE_PROVIDER_KEY_ACTIVATION | — | detect provider env vars by name/presence only |
| 6 | WAVE_PROVIDER_SUBSCRIPTION_RANKING | — | rank the smallest unlocking provider set |
| 7 | WAVE_EARNINGS_ANALYST_PROVIDER_DECISION | ✅ | test earnings-confirmed candidates + decide earnings/analyst provider |
| 8 | WAVE_NEWS_SENTIMENT_PROVIDER_DECISION | — | exhaust GDELT free; decide deep-history news provider |
| 9 | WAVE_OPTIONS_IV_SKEW_PROVIDER_DECISION | — | decide IV/skew provider only if a signal needs it |
| 10 | WAVE_SHORT_INTEREST_BORROW_ACTIVATION | — | attempt FINRA free short interest before any paid source |
| 11 | WAVE_TAIL_RISK_REPAIR_SIGNAL_TESTS | ✅ | fixed beta-tail / vol-quintile / sector-cap filters on the clean leads |
| 12 | WAVE_TRADE_IDEA_PROMOTION | — | promote promising leads to paper-review trade ideas (with trade-ready reason) |

3 scoring waves × 8 pre-registered hypotheses = 24 scoreable experiments; each scoring wave is ≥ 0.30
challenges/placebos (overall challenge fraction 0.375).

## The 14 data families (7 statuses, incl. ERROR)

`LOCAL_DATA_FOUND · FREE_NO_KEY_SOURCE_ACTIVATED · FREE_NO_KEY_SOURCE_ATTEMPTED_BUT_INSUFFICIENT ·
EXISTING_PROVIDER_KEY_ACTIVATED · PAID_PROVIDER_REQUIRED · NOT_RELEVANT_AFTER_TESTING · ERROR`.

Campaign result with **no provider keys present**: **3 LOCAL** (macro, sector, liquidity), **1
FREE_NO_KEY_ACTIVATED** (SEC filings), **8 FREE_NO_KEY_ATTEMPTED_BUT_INSUFFICIENT** (broad earnings,
analyst revisions, news, short interest, insider, 13F, guidance, fundamentals), **2 PAID_REQUIRED**
(transcripts/tone, options IV/skew). Each family carries its `best_provider`, `required_env_var`,
`approximate_cost`, `exact_endpoint`, `signals_unlocked`, `expected_event/quality gain`, `next_action`,
and a `hard_decision` — no vague "provider required".

## Provider decision

- **First subscription = FMP** — the smallest single key that attacks BOTH top blockers (broad earnings
  surprise + analyst estimate revisions) and additionally unlocks transcripts, guidance/press releases,
  fundamentals and a filings feed. `FMP_API_KEY` is the only env var needed first.
- **Free before paid, always:** FINRA (short interest) before any paid short source; GDELT (news) before
  any paid news source; SEC EDGAR (filings / insider / 13F / 8-K guidance) and SimFin/SEC-XBRL
  (fundamentals) collected free first.
- **Do not buy yet:** pure specialists that touch no top blocker — Polygon, NewsAPI, Tiingo, Quandl;
  Intrinio/Polygon options only if a candidate signal specifically needs IV/skew; deep-history news
  (Benzinga) only after GDELT is exhausted.
- Provider artifacts: decision matrix (per family×provider, free-ranked-first), priority ranking
  (FMP first paid), bundle recommendation, activation order (free → FMP → conditional specialists),
  cost/value, free-trial plan, expected signal impact, and a committed-safe `provider_acquisition_commands.ps1`
  with **placeholder** `$env:FMP_API_KEY = "<your_key>"` (never a real key).

## Campaign results (local data only, no keys)

`stop_reason = WAVE_BUDGET_EXHAUSTED`, `recommendation = PROMISING_ALPHA_SIGNAL_FOUND`; 12/12 waves,
3 cycles, 24/24 scored, challenge fraction 0.375; **confirmed 0 · promising 7 (clean 2 / limited 5) ·
provider-required 3 · rejected 8**.

| Lead | Events | Recent | EV @25bps | Matched lift | Recent lift | Tail (worst-decile) | Status |
|------|-------:|-------:|----------:|-------------:|------------:|--------------------:|--------|
| S8L-EARN-VOLSENS-20 (earnings × vol-sensitive) | 1,152 | 380 | **+0.5025%** | +0.526% | −0.033% | −17.24% | promising (provider-limited) |
| S8L-EARN-HIGHBETA-20 | 1,168 | 399 | +0.4164% | +0.358% | −0.094% | −17.40% | promising (limited) |
| S8L-RATES-EARNCONF-20 (= F20) | 692 | 394 | +0.3779% | +0.533% | +0.488% | −15.54% | promising (limited) |
| S8L-EARN-SECLEAD-20 | 933 | 411 | +0.2685% | +0.324% | +0.221% | −17.28% | promising (limited) |
| S8L-RATES-MACRO-20 (= S8E-011 anchor) | 11,881 | 6,320 | +0.1276% | +0.404% | +0.341% | −15.78% | promising (clean, full coverage) |
| S8L-RATES-MACRO-VOLCOMP-20 | 11,881 | 6,320 | +0.1276% | +0.404% | +0.341% | −15.78% | promising (clean) |

The anchor and F20 reproduce 8-E/8-K exactly (11,881 @ +0.128% and 692 @ +0.378%), confirming continuity
with the validated stack.

### Tail-risk repair: an honest negative

The fixed structural filters (extreme-beta exclusion via `cohort_low_beta`, top-vol-quintile exclusion via
`vol_compress`, sector cap) were applied to the clean macro leads with **no tuning after scoring**. All 8
tail-repair experiments were **REJECTED**: the low-beta filter collapsed coverage (11,881 → 455 events)
and turned EV negative without lifting the worst-decile mean. This negative is reported in full
(`tail_risk_repair_scoreboard.csv`), not hidden. The clean macro lead's tail risk therefore remains
**unrepaired by these fixed local filters** — walk-forward confirmation or provider breadth is the next
lever, not further filter tuning.

### Provider-expansion-required signals

3 signal families are testable/confirmable only with provider data (`provider_expansion_required_scoreboard.csv`):
news-sentiment × sensitivity (Benzinga / `BENZINGA_API_KEY`), options IV/skew × sensitivity
(Intrinio / `INTRINIO_API_KEY`), short-interest × sensitivity (Finnhub / `FINNHUB_API_KEY` after FINRA
free). Placeholder **specs only** (`data_family_unlocked_signal_specs.csv`) — never fabricated results.

## Trade-idea candidates

`trade_idea_candidate_registry.csv` / `best_trade_idea_candidates.csv` promote each promising lead to a
paper-review-only idea with `thesis`, `trigger_conditions`, `required_data_families`,
`current_validation_status`, event/EV/lift/tail metrics, `provider_dependency`, `next_validation_step`,
`promotion_status`, **`whether_trade_ready`** and **`reason_not_trade_ready`**.

**Nothing is trade-ready.** Every idea is `whether_trade_ready = False` because no signal is CONFIRMED:
the clean macro lead needs walk-forward + tail-repair confirmation; the higher-EV earnings candidates are
coverage/provider-limited (the earnings feed covers only ~75 tickers) and need the FMP backfill.

## 31 committed-safe artifacts

`research/output/phase8l_data_family_expansion_signal_factory/`:
phase8l_…factory.json, factory_state_summary.json, factory_run_log.csv, wave_registry.csv,
missing_data_family_matrix.csv, local_cache_discovery_report.csv, free_no_key_activation_report.csv,
provider_key_inventory.csv, provider_discovery_log.csv, provider_decision_matrix.csv,
provider_priority_ranking.csv, provider_bundle_recommendation.csv, provider_expected_signal_impact.csv,
provider_cost_value_report.csv, provider_free_trial_plan.csv, provider_activation_order.csv,
provider_acquisition_commands.ps1, data_family_unlocked_signal_specs.csv, tail_risk_repair_scoreboard.csv,
provider_expansion_required_scoreboard.csv, autonomous_signal_scoreboard.csv, confirmed_alpha_signals.csv,
promising_alpha_signals.csv, provider_required_signals.csv, rejected_alpha_signals.csv,
trade_idea_candidate_registry.csv, best_trade_idea_candidates.csv, validation_skeptic_report.csv,
multiple_testing_report.csv, research_director_decision.json, phase8m_next_plan.json.

Large runtime state lives on `D:\Stock_Prediction_app_data\data_family_expansion_signal_factory\`.

## How to run

```powershell
# one scoring cycle (validation)
python research/run_phase8l_data_family_expansion_signal_factory.py --once
# full self-refilling campaign, halt on a confirmed signal
python research/run_phase8l_data_family_expansion_signal_factory.py --resume --stop-on-confirmed
# long bounded campaign with liveness
python research/run_phase8l_data_family_expansion_signal_factory.py --time-budget-minutes 180 `
    --max-experiments 400 --resume --stop-on-confirmed --heartbeat-seconds 30
# after you obtain an FMP key (in YOUR shell only — never commit it)
$env:FMP_API_KEY = "<your_key>"
python research/run_phase8l_data_family_expansion_signal_factory.py --resume --activate-live --stop-on-confirmed
```

## Safety contract

Local data first; Norgate + on-disk FRED for price/macro/sector/cross-asset; no package install. Provider
keys detected by name/presence only, never printed. Every experiment pre-registered; thresholds fixed a
priori; ≥30% challenges per scoring wave. External data never faked (provider-gated families are
SPEC-ONLY; the revision proxy stays labelled and capped below CONFIRMED). No threshold tuning after
results, no factor-sign flipping, no weight optimization, no regime activation, no ML fit, no hidden
failures. No Paper Trader, no GCP, no deployment, no broker/orders/automation, no live trading signals.
No commit, no push.

## Tests

`tests/test_phase8l_data_family_expansion_signal_factory.py` — 45 tests: vocab exactness (12 waves /
14 families / 7 statuses / 31 artifacts / 11 keys), per-wave column legality, global id uniqueness,
≥30% challenges per scoring wave, anchor-lead rebuild, self-refill stop logic, matrix completeness with no
vague provider requirement, key-promotes-family, provider ranking (FMP first earnings+revisions, FINRA
before paid short, GDELT before paid news, first paid = FMP), bundle/activation-order free-before-FMP,
discovery never reads key values, placeholder PS1, spec-only unlocked signals with no fakes, trade-idea
trade-ready reason, source-level order/automation ban, and real `--once` + full-campaign runs (all 31
artifacts, 12-wave refill, tail-repair scored, earnings-confirmed scored, anchor reproduces S8E-011,
provider-required present, safety block clean, dry-run no-persist, resume continues, stop-file halts).

## Next phase (8-M)

`phase8m_next_plan.json`: acquire **FMP** (first paid) to unlock broad earnings + analyst revisions and
clear the ~75-ticker coverage blocker; exhaust FINRA / GDELT / SEC EDGAR free first; then re-run with
`--activate-live --stop-on-confirmed` to push the highest-EV earnings candidates and the clean macro lead
toward CONFIRMED via walk-forward + the broadened event base.
