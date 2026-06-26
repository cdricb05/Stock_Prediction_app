# Phase 8-T - Autonomous EODHD Alpha-Factory Daemon

Status: implemented + tested (offline, 14/14 targeted tests) and run live against the paid EODHD key.
Runner: `research/run_phase8t_autonomous_alpha_daemon.py`. **Decision and run numbers are in the
Status block at the bottom and in `research/output/phase8t_autonomous_alpha_daemon/`.** Nothing
committed, nothing pushed. Preview-only research - no orders, no automation, no Paper Trader / GCP.

## Why this phase exists

Phase 8-S ran ONE bounded acquisition + a 10-scenario scoring pass and promoted two earnings signals
(`surprise_sector_neutral`, `sue_standardized`). Phase 8-T is the **persistent daemon** that continues
*beyond* that single pass instead of stopping at one batch / one scenario family / one alpha. It:

1. **diagnoses** why the scoreable universe stopped at 299 of 301 priced tickers and emits the exact
   unscoreable names + reasons;
2. **searches** the repo + `D:\Stock_Prediction_app_data` for a broader local PRICE universe and, when
   none exists, emits a *bounded* EODHD-EOD expansion plan (never an unbounded full-market download);
3. **refreshes / repairs** EODHD coverage (bounded, skip-existing);
4. runs a **~40-scenario battery** across earnings, fundamentals, valuation, price/context, regime, and
   interaction families over **multiple autonomous cycles**;
5. validates each scenario with **extended robustness** (subperiod stability, placebo/challenge,
   transaction cost at 10/25/50 bps, sign consistency, multiple-testing control); and
6. promotes only durable alpha, emitting **Paper-Trader-preview-ready signal specs** and one terminal
   research decision.

It **reuses the Phase 8-S data layer + scoring primitives verbatim** (point-in-time event table,
rank-IC / quantile-spread / turnover / cost / regime machinery, Benjamini-Hochberg) and the Phase 8-R
EODHD transport + secret discipline underneath it. 8-T adds universe diagnosis, the larger battery,
stricter robustness gates, the autonomous cycle loop, and the preview specs.

## Autonomous workflow

- **A. Universe expansion / diagnosis.** The scoreable ceiling is the intersection of the
  EODHD-coverable universe with the local OHLCV cache (`phase7i_broad_price_history_free.csv`, 301
  priced tickers). Each priced-but-unscoreable ticker is reported with an exact reason. The daemon
  probes for a broader 500/1000/3000-name local price cache; none exists beyond phase7i, so the only
  honest expansion is a **bounded EODHD-EOD acquisition** for the local S&P-500 NAME list (523 names,
  Wikipedia constituents - no local OHLCV yet). That is the gated next action, not run here.
- **B. Refresh / repair.** One EODHD `fundamentals` request per not-yet-cached ticker; `skip_existing`
  means already-cached names cost 0 requests; checkpoints every 50; stops on invalid key / free-tier
  block / consecutive rate-limits. Reuses the proven 8-R transport + redaction + secret discipline.
- **C. Features.** On top of the 8-S base event table, 8-T adds price-context features (63d momentum,
  63d realized volatility, 63d beta, dollar-volume liquidity, market-drawdown regime), extended
  fundamentals (gross / operating / net margins, a quality composite, a balance-sheet-strength
  composite, quality improvement), a valuation proxy (trailing-4Q EPS / entry price), surprise
  asymmetry / magnitude encodings, and additive z-score interaction composites. **Every signal is
  oriented so higher == expected-better** (inverse signals like debt / volatility / beta are negated),
  so a genuine alpha shows up as a POSITIVE IC - no post-hoc sign flipping.
- **D. Validation (per scenario).** Monthly cross-sectional rank IC (mean, t, normal-approx p),
  quintile long-short spread (mean, t, monthly sign hit-rate), event hit rate, turnover,
  transaction-cost sensitivity at **10 / 25 / 50 bps**, rate-regime split, sector concentration,
  **subperiod stability** (both sample halves must keep the positive IC sign), **placebo/challenge**
  (within-month return shuffle + random-signal control), and **multiple-testing control** (Bonferroni +
  Benjamini-Hochberg across all tested scenarios).
- **E. Promotion gates (stricter than 8-S).** A signal is promoted only if it clears ALL of: `>=150`
  events; `>=10` months; **signed** mean IC `>= +0.03`; **signed** IC t `>= +2.0`; spread hit-rate
  `>= 0.55` (or a strongly compensating economic spread); survives **10 bps and stays positive at the
  hard 25 bps** cost gate; top sector `<= 60%`; right-sign IC in `>= 2` rate regimes; **stable across
  subperiods**; and Benjamini-Hochberg significant (or explicitly marked exploratory). Exploratory /
  challenge scenarios are reported but never promoted as confirmed alpha.
- **F. Autonomous loop.** The battery is partitioned into cycles (one family batch per cycle). Each
  cycle tests its batch, accumulates into the candidate registry + scoreboard, and the loop continues
  automatically until the battery is exhausted or `--max-cycles` is hit (then
  `READY_FOR_NEXT_AUTONOMOUS_CYCLE`). Multiple-testing control is finalized across all scenarios.

## No-look-ahead rules

Inherited verbatim from Phase 8-S: an earnings event is usable only if `reportDate <= as_of`,
`reportDate >= fiscal_period_end`, and `eps_actual` is realized; **entry price = first cache close
strictly after `reportDate`**; forward returns over 1/5/21/63 trading days (headline = 21d
benchmark-excess); fundamentals joined as-of `filing_date <= reportDate`; macro joined as-of
`reportDate` (backward). 8-T's added features keep the same discipline: price-context features are
computed only from closes *before* the entry index, and the valuation proxy uses the trailing-4Q EPS
known at the announcement over the (later) entry price. Analyst estimates/ratings are excluded from
scoring (not PIT-safe).

## Decision values

`ALPHA_FOUND_READY_FOR_PAPER_TRADER_REVIEW` (promoted, none new vs 8-S) |
`MORE_ALPHA_FOUND_READY_FOR_PAPER_TRADER_REVIEW` (promoted, >=1 new family) |
`NO_ALPHA_FOUND_AFTER_EXHAUSTIVE_EODHD_CAMPAIGN` | `READY_FOR_NEXT_AUTONOMOUS_CYCLE` (max-cycles hit) |
`HARD_BLOCKER_REQUIRES_USER_ACTION` | `ERROR`.

## Committed-safe artifacts

`research/output/phase8t_autonomous_alpha_daemon/` (metadata only - never a payload, never a key):
`phase8t_autonomous_alpha_daemon.json` (main report), `daemon_run_log.csv`, `cycle_summary.csv`,
`universe_expansion_report.csv`, `unscoreable_tickers_report.csv`, `acquisition_progress.csv`,
`data_coverage_matrix.csv`, `feature_catalog.csv`, `scenario_candidate_registry.csv`,
`scenario_test_plan.csv`, `scenario_scoreboard.csv`, `rank_ic_report.csv`, `spread_report.csv`,
`hit_rate_report.csv`, `turnover_report.csv`, `transaction_cost_sensitivity.csv`,
`sector_concentration_report.csv`, `regime_split_report.csv`, `subperiod_stability_report.csv`,
`multiple_testing_report.csv`, `placebo_challenge_report.csv`, `promoted_alpha_signals.csv`,
`rejected_alpha_signals.csv`, `paper_trader_preview_signal_specs.csv`, `final_research_decision.json`,
`phase8u_next_plan.json`, `secret_safety_audit.csv` (plus `point_in_time_audit.csv` and the
informative `raw_storage_manifest.csv` / `normalized_storage_manifest.csv`). Raw + normalized EODHD
payloads live ONLY under the gitignored `research/data/eodhd/{raw,normalized}/` trees.

## Secret discipline

`EODHD_API_KEY` is read ONLY from the environment, never printed, never written to disk. Every persisted
URL is redacted; a leak scan over the committed artifacts confirms it is clean. The low-level EODHD
transport, redaction, persistence and gitignore enforcement are reused verbatim from Phase 8-R / 8-S.

## Run

```powershell
# Offline campaign on whatever EODHD data is already cached locally (no network, no key):
python research/run_phase8t_autonomous_alpha_daemon.py

# Bounded live refresh + campaign (requires a paid EODHD_API_KEY in the env):
$env:EODHD_API_KEY = '<PAID_EODHD_KEY>'
python research/run_phase8t_autonomous_alpha_daemon.py --live --max-tickers 500 --max-requests 5000 --max-cycles 8

# Test (fully offline; injected transport, no key, no network):
python -m pytest tests/test_phase8t_autonomous_alpha_daemon.py -q
```

## Constraints honored

Existing installed packages only (numpy + pandas; no scipy - IC significance uses a normal-approx
p-value; placebo uses a seeded numpy RNG). No package install. Bounded acquisition + bounded cycle loop.
No raw/normalized provider data committed. No API key printed or written. No Paper Trader, no GCP, no
deploy, no broker / order / automation logic. No full Phase-8 regression (targeted tests only). No
commit. No push.

## Status (live run)

Live refresh + campaign completed against the paid EODHD key on `as_of = 2026-06-26` (terminal,
mode `live_refresh_and_campaign`). Numbers from
`research/output/phase8t_autonomous_alpha_daemon/phase8t_autonomous_alpha_daemon.json`.

**Terminal decision: `MORE_ALPHA_FOUND_READY_FOR_PAPER_TRADER_REVIEW`.**

- **Universe:** 301 priced tickers; 1 not-yet-cached ticker refreshed in 1 request (returned no
  earnings - an ETF). **299 scoreable**; **2 unscoreable** - `SPY` (no EODHD earnings; it is an index
  ETF) and `SNHIY` (earnings + fundamentals present but no PIT-usable event with forward-price overlap).
  No broader local OHLCV cache exists beyond phase7i; expansion to the 523-name S&P-500 list requires a
  bounded EODHD-EOD acquisition (gated next action).
- **Point-in-time panel:** 29,032 usable PIT earnings events across 299 tickers, 12 sectors,
  `reportDate` 2016-06-23 .. 2026-05-20 (unchanged from 8-S - same cache).
- **Campaign:** 6 autonomous cycles, **43 scenarios** across earnings / fundamentals / valuation /
  price / regime / interaction families. Bonferroni + Benjamini-Hochberg across all 43.
- **Promoted (clear every stricter gate): 4 -** of which **3 are NEW** beyond the Phase 8-S pair:
  - `surprise_sector_neutral` (`surprise_pct`, sector-neutral): IC 0.0514, t 2.96, spread +1.08%/mo,
    hit-rate 0.642, net +0.58%/mo @25 bps, n=27,279. *(reconfirms 8-S)*
  - `surprise_x_quality` (`z(surprise_pct)+z(roa)`): IC 0.047, t 2.97, spread +1.03%/mo, hit-rate 0.65,
    net +0.54%/mo @25 bps, n=26,839. *(NEW - additive, monotone construction; unlike the 8-S*
    *multiplicative `surprise x roa` which flipped sign, this is a genuine positive-IC interaction.)*
  - `positive_surprise_asymmetry` (`surprise_pos`): IC 0.0488, t 3.08, spread +0.51%/mo, hit-rate
    0.592, net +0.02%/mo @25 bps, n=28,654, beats placebo. *(NEW)*
  - `surprise_magnitude` (`magnitude_score`): IC 0.0411, t 2.68, spread +0.66%/mo, hit-rate 0.608,
    net +0.16%/mo @25 bps, n=29,032. *(NEW)*
  - Caveat: all four are **earnings-surprise-family** signals (three are monotone surprise encodings;
    the fourth adds ROA quality), so they are correlated, not four independent bets - treat as one
    well-evidenced surprise/PEAD theme with a quality tilt, for **manual** review.
- **Notably rejected under the stricter gates:** `sue_standardized` - promoted by 8-S at the 10 bps
  gate, but its quintile spread is only +0.26%/mo and goes **negative (-0.23%/mo) after 25 bps** and it
  is not BH-significant, so 8-T does not promote it. `earnings_surprise` (raw) and `sue_sector_neutral`
  also fail the 25 bps cost gate. The fundamentals, valuation, price-context and regime families
  produced no promotable alpha on this 299-name cross-section.
- **Robustness:** every promoted signal is subperiod-stable (both halves positive IC), survives the
  25 bps cost gate, is within the 60% sector cap, and is right-signed across rate regimes;
  multiple-testing controlled across all 43 scenarios.
- **Secret discipline:** leak scan clean over 30 committed-safe files; key never printed/written;
  `research/data/eodhd/{raw,normalized}/` force-gitignored.
- **Safety:** preview-only; no orders, no automation, no broker, no Paper Trader / GCP touch; not
  committed, not pushed.

**Exact next step:** review `paper_trader_preview_signal_specs.csv`, then surface the four promoted
signals (one correlated surprise/PEAD-with-quality theme) as PREVIEW-ONLY ranking ideas in the Paper
Trader daily-review cockpit (manual review; no orders). The deeper next-data move (`phase8u_next_plan`)
is the bounded EODHD-EOD universe expansion to widen the 299-name cross-section.
