# Phase 8-X - Autonomous Strong Alpha Discovery Campaign

Status: implemented + tested (15/15 targeted tests, fully offline) and **executed against the live
545-ticker expanded universe**. Runner: `research/run_phase8x_autonomous_strong_alpha_discovery.py`.
**Decision + numbers are in the Status block at the bottom and in
`research/output/phase8x_autonomous_strong_alpha_discovery/`.** Nothing committed, nothing pushed.

## Why this phase exists

The earnings-surprise alpha family has now been chased to its limit on the EODHD data:

- Phase 8-T promoted four earnings-surprise signals on the original **299-ticker** cross-section.
- Phase 8-V's matched **299 -> 545** universe expansion **DILUTED** them (4 promoted -> 0;
  `EXPANDED_UNIVERSE_WEAKENS_ALPHA`).
- Phase 8-W attributed the decay to a low-information new cohort and found the alpha survives **only
  CONSTRAINED** to the old cohort / high-liquidity / single sector (`CONSTRAINED_ALPHA_SURVIVES`).

A constrained, exploratory signal is **not** a strong, robust, money-making alpha. Phase 8-X is the
autonomous discovery campaign that keeps searching the **expanded 545-ticker universe** - across the
full breadth of broad alpha families **and** transparent walk-forward factor-ensemble MODELS - until
it either finds a genuinely STRONG broad alpha or proves the current EODHD data families are
exhausted. It does **not** salvage, preview, or productize the constrained 8-W signal; constrained /
weak candidates are logged as `CONSTRAINED_NOT_GOOD_ENOUGH` and the search continues.

## Reuse

The cross-sectional scoring core is reused verbatim from Phase 8-T (`evaluate_ext`, `gate_reasons`,
`scenario_battery`, `prepare_signals_ext`) over Phase 8-S's point-in-time `build_event_table` and
Benjamini-Hochberg control, plus Phase 8-W's expanded event-table / cohort-tag / liquidity-proxy
builders. The phase reads only cached data (the gitignored 8-V expanded panel + EODHD earnings cache);
it never calls a network or a key.

## What 8-X adds

1. **A stricter STRONG-alpha gate** (`strong_gate_reasons`): >=500 scoreable tickers, >=30,000 PIT
   events, IC t-stat **>= 3.0**, BH-significant (q=0.10), positive net spread after 25bps, spread
   hit-rate >= 0.58 (or a strongly compensating net spread), positive IC in **BOTH** the old and new
   cohorts (not old-cohort-only), positive IC in **BOTH** pre/post-2020 halves, and top-sector share
   <= 60% (not single-sector). Exploratory/challenge probes are never promotable.
2. **A durable autonomous research loop** (`run_campaign` + `autonomous_resume_state.json`): cycle by
   cycle, each cycle tests one not-yet-tested batch (a scenario family or a model set), accumulates
   the hypothesis registry + promoted/rejected/constrained registries, and **persists resume state
   every cycle**. Re-running with unchanged inputs re-tests nothing (the input hash folds in the
   universe size). The loop stops only on a terminal condition (strong found / exhausted / bound hit);
   a weak or constrained candidate never stops the search.
3. **A transparent walk-forward factor-ensemble MODEL layer** (`build_walk_forward_ensemble`): each
   model is an additive, within-month z-scored blend of oriented factors; weights are estimated
   **out-of-sample** on a rolling train window (equal / sign-of-trailing-IC / trailing-IC) and applied
   to the held-out test window. Out-of-sample monthly IC and **decile** (10-bucket) long-short spreads
   are reported per model, alongside transaction-cost sensitivity, turnover, cohort stability,
   sector concentration, subperiod stability, and multiple-testing control.

## Hypothesis space

48 scenarios across the broad families the brief enumerates (earnings surprise / SUE / acceleration /
post-earnings drift; fundamentals quality / improvement / valuation / growth / profitability /
balance-sheet strength / margin expansion; price momentum / reversal / low-vol / beta / liquidity /
sector-relative strength; macro-regime-conditioned; cross-factor interactions) plus 12 transparent
walk-forward factor-ensemble models (earnings / quality / value-price / kitchen-sink blends x
equal / ic-sign / ic-weighted) = **60 hypotheses across 10 autonomous cycles**.

## Terminal decisions

`STRONG_ALPHA_FOUND` (>=1 candidate clears the full strong gate) | `NO_STRONG_ALPHA_FOUND_CURRENT_DATA`
(bounded search hit max_cycles/max_scenarios before exhausting the space) | `NEEDS_NEW_DATA_FAMILY`
(space exhausted, no strong broad alpha - the binding constraint is the data, not scenario design) |
`HARD_BLOCKER_REQUIRES_USER_ACTION` (the gitignored expanded panel is absent) | `ERROR`.

## Required artifacts (19, committed-safe metadata only)

`research/output/phase8x_autonomous_strong_alpha_discovery/`:
`phase8x_autonomous_strong_alpha_discovery.json`, `autonomous_cycle_log.csv`,
`hypothesis_registry.csv`, `scenario_scoreboard.csv`, `strong_alpha_candidates.csv`,
`rejected_hypotheses.csv`, `constrained_not_good_enough.csv`, `model_candidate_scoreboard.csv`,
`walk_forward_results.csv`, `decile_spread_report.csv`, `transaction_cost_report.csv`,
`turnover_report.csv`, `cohort_stability_report.csv`, `sector_concentration_report.csv`,
`subperiod_stability_report.csv`, `multiple_testing_report.csv`, `data_family_exhaustion_report.csv`,
`phase8y_next_plan.json`, `secret_safety_audit.csv`. (Plus durable `autonomous_resume_state.json` +
`phase8x_run_log.csv`.) No raw or normalized provider data is written; the expanded panel + earnings
remain under the gitignored `research/data/eodhd/` trees.

## Run

```powershell
# Offline discovery campaign over the cached 8-V expanded panel + EODHD earnings (no network, no key):
python research/run_phase8x_autonomous_strong_alpha_discovery.py
# Resume (skip already-tested hypotheses with unchanged inputs):
python research/run_phase8x_autonomous_strong_alpha_discovery.py --resume
# Test (fully offline; synthetic strong / diluted cohorts, no key, no network):
python -m pytest tests/test_phase8x_autonomous_strong_alpha_discovery.py -q
```

## Constraints honored

Existing installed packages only (numpy + pandas). No package install. No external API call, no key
read or printed. No raw/normalized provider data written or committed. No Paper Trader, no GCP, no
deploy, no broker / order / automation logic. The constrained 8-W signal is **not** productized. No
full Phase-8 regression - targeted tests only. No commit. No push.

## Status (live run)

Executed against the live gitignored 8-V expanded panel on `as_of = 2026-06-26`.

**Terminal decision: `NEEDS_NEW_DATA_FAMILY`.**

- **Universe:** 545 scoreable tickers / 38,725 PIT events (old 29,032 / new 9,693) - the broad
  universe floor (>=500 tickers, >=30,000 events) **is** met, so the conclusion is about information
  content, not sample size.
- **Search:** 10 autonomous cycles, 60 hypotheses (48 scenarios + 12 walk-forward ensemble models);
  hypothesis space fully exhausted.
- **No strong alpha:** **0** candidates cleared the strong gate. Best broad candidate
  `earnings_acceleration` IC 0.0255, t **2.24** - well short of the t>=3.0 bar. Best by family:

  | alpha family | best hypothesis | best IC t | strong? |
  |---|---|---|---|
  | earnings surprise / drift | earnings_acceleration | 2.24 | no |
  | fundamentals quality | balance_sheet_strength / quality_composite | 1.97 | no |
  | cross-factor interactions | surprise_x_quality | 1.78 | no |
  | macro-regime conditioned | surprise_easy_curve | 1.58 | no |
  | transparent factor ensembles | quality_blend (equal, walk-forward) | 1.18 | no |
  | price momentum/reversal/low-vol/beta | price_momentum_21 | 0.61 | no |
  | valuation | earnings_yield_value | 0.46 | no |

  The surprise family confirms the 8-W dilution: on the combined 545 universe its new-cohort IC is
  ~0 / negative (e.g. positive_surprise_asymmetry new-cohort IC -0.0088, sue_sector_neutral -0.0011),
  so even the statistically-best surprise read sits below t=3 and fails the both-cohorts gate. No
  candidate even reached the `CONSTRAINED_NOT_GOOD_ENOUGH` bar (combined t>=3 + BH) on the full
  universe - they are plainly rejected as not strong.
- **Recommended new data family:** `analyst_estimate_revisions + short_interest +
  options_implied_vol_skew + news_social_sentiment`. The EODHD earnings / fundamentals / price
  families are exhausted for broad strong alpha; the binding constraint is their information content,
  not scenario design or model class, so a genuinely NEW, orthogonal data family is required next.
- **Safety:** preview-only; no orders, no automation, no broker, no Paper Trader / GCP touch; no
  network, no key; the constrained 8-W signal was NOT productized. Leak scan clean over 19
  committed-safe files. Not committed, not pushed.

**Exact next step:**
Phase 8-Y - acquire a NEW orthogonal data family (analyst estimate-revision breadth, short interest /
days-to-cover, options-implied volatility & skew, news / social sentiment) under the same bounded,
secret-safe, gitignored-payload discipline, then re-run this discovery campaign. Do **not** productize
the constrained earnings-surprise signal.
