# Phase 7-B — Validation Harness Foundation (v1)

**Track A (quant brain) research infrastructure. Offline, leakage-safe, reusable.**
No network, no provider call, no paid API, no API key read or required, no model
trained or deployed, **no factor alpha built, no factor weights optimized**, no
Paper Trader / GCP work, no orders / broker / automation, no binary artifact, no
commit, no push.

- **Phase:** 7-B
- **Status:** Implemented and self-validated (pending owner endorsement to proceed to 7-C)
- **Constitution:** [project_charter_sp500_multifactor_ranking_v1.md](project_charter_sp500_multifactor_ranking_v1.md)
- **Predecessor:** [adr_phase7a_ranking_engine_reset.md](adr_phase7a_ranking_engine_reset.md)
- **Recommendation:** `READY_FOR_PHASE7C_MULTIFACTOR_RANKING_ENGINE`

---

## Why this phase exists

The charter (Section 6 / charter Phase 0) is explicit: **build the measuring
instrument before building more signals.** Phase 7-A reset the project to a two-system
ranking + risk design and recommended `READY_FOR_PHASE7B_VALIDATION_HARNESS`. Every
prior negative result — Phase 5-G2 `NO_INCREMENTAL_EVENT_EDGE`, Phase 6-A degraded
selection — shares one root cause: claims were judged *without* a single, trusted,
leakage-safe instrument that all later factors and ranking models must pass before
they are considered real.

Phase 7-B builds that instrument. It does **not** build factors, does **not** optimize
weights, and makes **no** alpha claim. It builds the harness and *self-validates* it on
deterministic synthetic data, then emits the capability / metric / gate catalogues and
the Phase 7-C hand-off.

## What the harness provides (reusable, importable by Phase 7-C+)

All entry points live in
[research/run_phase7b_validation_harness_foundation.py](../research/run_phase7b_validation_harness_foundation.py)
(no work runs at import time) and reuse
[research/metrics.py](../research/metrics.py) for rank-IC (charter requirement VR-11,
reuse-first):

| # | Capability | Entry point(s) | Status |
|---|---|---|---|
| 1 | Walk-forward splits (expanding train → forward test, embargo gap) | `walk_forward_splits` | implemented |
| 2 | Purged k-fold with embargo (label-overlap purge + post-test embargo) | `purged_kfold_splits` | implemented |
| 3 | Single-touch holdout ledger (OOS is a spent budget) | `HoldoutLedger` | implemented |
| 4 | Multiple-testing counter + deflated Sharpe | `MultipleTestingTracker`, `expected_max_sharpe`, `deflated_sharpe_ratio` | implemented |
| 5 | Transaction-cost model (bps × turnover → gross vs net) | `turnover_from_weights`, `apply_transaction_costs` | implemented |
| 6 | Metric suite (mean rank IC, IC t-stat, yearly IC, decile/quintile spread, Sharpe, max drawdown, turnover, cost-adjusted return) | `period_rank_ics`, `ic_summary`, `yearly_ic`, `quantile_spread`, `sharpe`, `max_drawdown` | implemented |
| 7 | Regime / sub-period decomposition | `decompose_by_group`, `yearly_ic` | implemented |
| 8 | Sensitivity-surface framework (measures robustness; never optimizes) | `sensitivity_surface` | framework template |
| 9 | Safety self-checks (no-lookahead / no same-day leakage / no production model) | `assert_strictly_forward_labels` + safety gates | implemented |
| 10 | Equal-weight composite baseline (the hard benchmark; no weight optimization) | `equal_weight_composite` | implemented |

## Leakage safety

Every split is leakage-safe by construction:

- **Walk-forward:** a train sample is kept iff `eval_idx < test_lo` **and**
  `label_end_idx ≤ test_lo − 1 − embargo` — so its label fully resolves at least
  `embargo` periods before the test window opens. The reported `min_embargo_gap`
  proves the gap held.
- **Purged k-fold:** a train sample is purged when its label interval
  `[eval, label_end]` overlaps the test interval, and embargoed when its decision
  period falls within `embargo` periods immediately after the test block.
- **No same-day forward leakage:** `assert_strictly_forward_labels` rejects any label
  that resolves on or before its decision period.
- **Placebo:** a deterministic within-period label permutation must collapse the IC
  toward zero — any residual signal would indicate look-ahead.

## Self-check result (grades the instrument, on synthetic data)

The harness runs on a seeded synthetic cross-sectional panel (96 periods × 60 names)
with a *known* embedded signal. This is test data for the instrument — **not** a
factor, **not** alpha, and it uses no market data.

- Honest mean rank IC ≈ **0.48** (t ≈ 45) — the known signal is recovered.
- Placebo mean rank IC ≈ **0.00** — the within-period shuffle collapses, so no
  look-ahead leaks through the splits.
- Walk-forward folds leakage-clean (every `min_embargo_gap ≥ embargo`); purged k-fold
  drops the deliberately-overlapping train row.
- Holdout single-touch enforced (second touch flagged as a spent-budget violation).
- Cost model nets below gross; deflated Sharpe in `[0,1]` and falls as trials rise.
- Regime and yearly IC decomposition reported (stationarity not assumed).
- **Gate matrix: 24 PASS / 0 FAIL.**

## What is still only a template

- **Sensitivity surface:** the framework is live and tested, but there is no real
  factor model to sweep yet (a synthetic concave evaluator stands in). It will be
  pointed at real factors in Phase 7-C+.
- **Deflated Sharpe:** uses default skew = 0 / kurt = 3 until a real net-return series
  exists.
- **`multiple_testing_tracker_template.csv`:** an illustrative ledger showing the
  columns every future configuration test must log; real trials are recorded at
  evaluation time by `MultipleTestingTracker`.
- **Cost model `cost_bps`:** an assumption until a real book's turnover and fills exist.

## Recommendation

**`READY_FOR_PHASE7C_MULTIFACTOR_RANKING_ENGINE`** — the measuring instrument exists,
is leakage-safe by construction, and passes every capability and safety gate on the
self-check. Phase 7-C (Multi-Factor Ranking Engine, System 1) may proceed, grading
every factor and the equal-weight composite *through this harness*.

Allowed recommendation values: `READY_FOR_PHASE7C_MULTIFACTOR_RANKING_ENGINE` /
`NEEDS_VALIDATION_REVIEW` / `BLOCKED` / `ERROR`.

## Committed-safe artifacts

Written to `research/output/phase7b_validation_harness_foundation/`:

- `phase7b_validation_harness_foundation.json` — main report (capabilities, self-check, gates, recommendation)
- `validation_harness_requirements.csv` — the 10 core requirements → capability + status + acceptance
- `validation_metric_catalog.csv` — metric name / definition / what good looks like
- `validation_gate_matrix.csv` — capability + safety gate results
- `multiple_testing_tracker_template.csv` — template ledger for multiple-testing discipline
- `walk_forward_split_plan.csv` — the concrete leakage-safe split plan from the self-check
- `phase7c_next_plan.json` — the Phase 7-C hand-off (entry points + what to build / not build)

Code + tests:

- [research/run_phase7b_validation_harness_foundation.py](../research/run_phase7b_validation_harness_foundation.py)
- [tests/test_phase7b_validation_harness_foundation.py](../tests/test_phase7b_validation_harness_foundation.py) (21 tests, all passing)

## How Phase 7-C uses it

Import the entry points and grade each factor + the equal-weight composite through the
harness: walk-forward + purged CV, deflated Sharpe with the config counter, per-regime
and per-year decomposition, net-of-cost metrics, all against the equal-weight
benchmark. **No factor-weight optimization** (forbidden until the harness is trusted
*and* the owner endorses a weighting philosophy — charter Section 5). The regime
overlay (System 2) stays deferred to Phase 7-E.

## Safety contract

Research infrastructure only · zero network / provider call · no Alpha Vantage / paid
API · no model trained or deployed · **no factor alpha built · no factor weights
optimized** · no database / migration touched · no strategy / shadow test · no orders /
broker / automation · no Paper Trader / GCP / deploy · committed-safe text artifacts
only · no commit · no push.
