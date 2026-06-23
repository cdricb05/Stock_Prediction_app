# Phase 7-C — Multi-Factor Ranking Engine Foundation (System 1, v1)

**Track A (quant brain) research. Offline, point-in-time, leakage-safe, equal-weight.**
No network, no provider call, no paid API, no API key read or required, no model
trained or deployed, **no factor-weight optimization**, no Paper Trader / GCP work, no
orders / broker / automation, no live data, no binary artifact, no commit, no push.
Reads only local files (the Phase 2K-G price panel READ ONLY on D:, and committed SEC
fundamentals / sector artifacts on C:). Writes nothing to D:.

- **Phase:** 7-C
- **Status:** Implemented and harness-graded (pending owner review)
- **Constitution:** [project_charter_sp500_multifactor_ranking_v1.md](project_charter_sp500_multifactor_ranking_v1.md)
- **Predecessors:** [adr_phase7a_ranking_engine_reset.md](adr_phase7a_ranking_engine_reset.md), [phase7b_validation_harness_foundation_v1.md](phase7b_validation_harness_foundation_v1.md)
- **Measured by:** the Phase 7-B harness (`research/run_phase7b_validation_harness_foundation.py`)
- **Recommendation:** `MULTIFACTOR_RANKING_ENGINE_WEAK`

---

## Why this phase exists

Phase 7-A reset the project to a two-system ranking + risk design; Phase 7-B built and
self-validated the measuring instrument. Phase 7-C builds the **first System 1 ranking
engine** and grades every factor and the composite *through that instrument*. This is a
cross-sectional ranking engine — **not** a production model, **not** a single-name
prediction oracle, **not** an alpha claim. Its job is to produce an honest, leakage-safe
measurement of whether an equal-weight multi-factor composite ranks the universe better
than a price-only baseline.

## What was built

A transparent, equal-weight, cross-sectional multi-factor ranking engine
([research/run_phase7c_multifactor_ranking_engine.py](../research/run_phase7c_multifactor_ranking_engine.py)):

1. **Data inventory** of the local sources available for factor construction.
2. **Factor buckets from real local data only.** No bucket is faked.
3. **Cross-sectional normalization** per month: sector-neutralize → z-score → winsorize ±3.
4. **Equal-weight composite** across approved buckets (reuses the Phase 7-B
   `equal_weight_composite`; **no learned weights**).
5. **Harness grading** of each factor and the composite: mean rank IC, IC t-stat, yearly
   IC, decile/quintile spread, turnover, cost-adjusted spread, Sharpe, max drawdown,
   deflated Sharpe with a real multiple-testing tracker, walk-forward + purged-CV split
   plan, single-touch holdout, and a robust within-period placebo.

## Universe & cadence

- **Universe:** 127 S&P names present in all three sources (price ∩ annual fundamentals ∩
  sector map); the SPY benchmark is excluded from the ranked set.
- **History:** 2016-01 … 2026-06, **monthly** (month-end scoring, next-month forward
  return as the label — strictly forward).

## Factor buckets

| Bucket | Source | Definition | Available | Approved |
|---|---|---|---|---|
| Momentum | price | 12-1 momentum `close[m-1]/close[m-13]-1` (skips most recent month) | yes | yes |
| Low-volatility | price | −(trailing 126-day realized daily-return vol); lower vol = better (inverted) | yes | yes |
| Value | fundamentals | earnings yield = latest annual diluted EPS / price (E/P) | yes | yes |
| Quality | fundamentals | ROE = latest annual net income / shareholder equity | yes | yes |
| Growth | fundamentals | revenue YoY = latest annual revenue / prior annual revenue − 1 | yes | yes |
| **Revisions / sentiment** | earnings-estimate panel | — | **no** | **no** |

**Why revisions/sentiment is unavailable (not faked):** the local
`combined_fundamental_earnings_panel.csv` is header-only (0 data rows). There is no
estimate-revision / sentiment signal to build, so the bucket is marked unavailable per the
charter's "do not fake it" discipline.

**Approval is breadth-and-history only, decided independently of IC.** A bucket joins the
equal-weight composite if it is measurable with adequate cross-section and span (≥ 24
monthly periods each clearing the 20-name minimum). Whether a factor *works* is reported
honestly by the scoreboard and is **never** used to admit or exclude it — so bucket
selection cannot be gamed against the test. The composite averages whichever approved
factors are present per (month, ticker), so a fundamentals bucket that becomes available
later simply contributes from its first live month onward.

## Leakage safety

- **Forward labels** are next-month returns that resolve strictly after the scoring month
  (`assert_strictly_forward_labels`: 0 violations).
- **Fundamentals** are activated only when `availability_datetime` is **strictly before**
  the scoring month-end — no same-day, no future fundamentals. Annual 10-K only (excludes
  10-K/A amendments), first-reported preferred.
- **Momentum** is 12-1 (skips the most recent month).
- **Placebo:** a within-period label permutation breaks the score↔label pairing; the
  *average* shuffled mean rank IC over 100 permutations must collapse to ~0. It did
  (≈ 0.0003), confirming no structural leakage. Judging the average (not a single draw)
  makes the test robust when the honest signal is itself near zero.
- All rank-IC math is reused from `research/metrics.py` via the Phase 7-B harness (VR-11).

## Result (honest measurement, not an edge claim)

| Series | Mean rank IC | IC t-stat | n months | Quintile spread |
|---|---|---|---|---|
| Price-only baseline (momentum + low-vol) | **−0.0123** | −0.72 | 121 | −0.0040 |
| Equal-weight composite (all 5 approved) | **−0.0074** | −0.50 | 121 | −0.0028 |
| **Incremental (composite − baseline)** | **+0.00495** | — | — | — |

- **Per-factor IC:** momentum +0.007, low-volatility **−0.034** (t = −2.1), value +0.021,
  quality −0.010, growth +0.010. In this large-cap universe over 2016-2026 the low-vol
  factor had a **negative** IC — higher-beta names outperformed in a tech-led bull regime.
  Signs are **not** flipped to chase IC (that would be the exact multiple-testing
  self-deception the charter forbids); the equal-weight composite carries documented
  economic priors as-is.
- The fundamentals add a small **positive** incremental (+0.00495 mean rank IC), but it
  falls **just short** of the **+0.005** success gate, and the composite IC remains
  negative (−0.0074) — **no demonstrated edge**.
- **Holdout (single-touch, terminal 20%):** incremental IC = +0.026 — fundamentals helped
  more in the recent window. Reported for context only; it does **not** override the
  full-sample gate, and the holdout was touched exactly once.
- **Net of cost** (10 bps × turnover, ~0.17 mean quintile turnover): composite quintile
  spread net −0.0035; composite Sharpe −0.27; max drawdown −0.45. Multiple-testing:
  8 trials registered, deflated Sharpe reported.

## Success gate

> Proceed only if the equal-weight composite improves mean rank IC by ≥ **+0.005** over
> the same-universe price-only baseline and passes leakage/placebo/safety.

**Not cleared.** Incremental = +0.00495 (< 0.005) and the composite IC is negative.
Leakage, placebo, and all safety gates pass. → `MULTIFACTOR_RANKING_ENGINE_WEAK`.

Gate matrix: **18 PASS / 1 FAIL / 2 WARNING** (FAIL = the incremental-IC success gate;
WARNINGs = composite IC not significant, and the sector map is not strictly point-in-time).

## What is still only a template or caveated

- Sector neutralization uses a **static current-as-of** sector map (`point_in_time=false`);
  membership is stable, but it is not strictly point-in-time — flagged as a WARNING.
- **Value** uses adjusted price against reported (unadjusted-basis) annual EPS — a caveated
  share-free E/P.
- Fundamentals are **annual 10-K only** (no TTM quarterly blend), so value/quality/growth
  are stale up to ~1 year.
- Revisions/sentiment is **unavailable** (empty local panel).
- Cost bps and the quintile-portfolio construction are modelling assumptions, not a real book.

## Recommendation

**`MULTIFACTOR_RANKING_ENGINE_WEAK`** — the engine is built, point-in-time, leakage-safe,
and fully harness-graded, but the equal-weight composite does **not** beat the price-only
baseline by the required +0.005 and shows no positive edge in this universe/sample. This is
a faithful negative-leaning result, not a failure of the build: the instrument did its job.

Allowed values: `MULTIFACTOR_RANKING_ENGINE_CONFIRMED` / `MULTIFACTOR_RANKING_ENGINE_WEAK` /
`NEEDS_FACTOR_DATA_FOUNDATION` / `NEEDS_VALIDATION_REVIEW` / `ERROR`.

## Committed-safe artifacts

Written to `research/output/phase7c_multifactor_ranking_engine/`:

- `phase7c_multifactor_ranking_engine.json` — main report (universe, factors, composite vs baseline, leakage, gates, recommendation)
- `factor_data_inventory.csv` — each local source: path, rows, tickers, date range, point-in-time, used-for
- `factor_catalog.csv` — each bucket: definition, orientation, available, approved, coverage, note
- `factor_scoreboard.csv` — per-factor IC / t-stat / spread / coverage / approved
- `composite_scoreboard.csv` — baseline vs composite, incremental, net-of-cost, Sharpe, drawdown, holdout
- `validation_gate_matrix.csv` — capability + success + safety gates
- `multiple_testing_tracker.csv` — one row per graded configuration + deflated Sharpe
- `phase7d_next_plan.json` — hand-off to Phase 7-D (Risk Decomposition)

Code + tests:

- [research/run_phase7c_multifactor_ranking_engine.py](../research/run_phase7c_multifactor_ranking_engine.py)
- [tests/test_phase7c_multifactor_ranking_engine.py](../tests/test_phase7c_multifactor_ranking_engine.py) (18 tests, all passing)

## Recommended next phase

**Phase 7-D — Risk Decomposition** (charter Phase 4.5): from the composite ranking, form a
measurement-only book and decompose book-level exposures (market beta, sector concentration,
factor tilts, idiosyncratic share) into a CFO-readable consolidated risk view. Measurement
only — zero hedging, zero orders. Factor-weight optimization and the System 2 regime overlay
(7-E) remain deferred. A future revisit of System 1 should broaden the universe beyond 127
large caps and add a TTM-quarterly fundamentals blend before any weighting philosophy is
considered.

## Safety contract

Research only · zero network / provider call · no Alpha Vantage / paid API · no model
trained or deployed · **no factor weights optimized** · point-in-time fundamentals (no
future data) · no database / migration touched · no strategy / shadow test · no orders /
broker / automation · no Paper Trader / GCP / deploy · D: read-only (nothing written) ·
committed-safe text artifacts only · no commit · no push.
