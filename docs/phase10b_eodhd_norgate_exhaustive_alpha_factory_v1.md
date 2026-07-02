# Phase 10-B — EODHD + Norgate Exhaustive Paid-Subscription Alpha Factory (v1)

## Why this phase exists (direction correction over 10-A)

Phase 10-A probed every visible market-data key in priority order and concluded
`ACCESSIBLE_MISSING_ALPHA_DATA_EXHAUSTED_NO_STRONG_ALPHA` — but most of its providers
(FMP / Finnhub / Polygon / Nasdaq) were **entitlement-blocked 403s**. The user is **not paying for
FMP**, so those 403s are expected and must not drive the research plan.

The user's **actual paid subscriptions** are:

1. **Norgate US Stocks Diamond Package** (expires 2026-12-24) — the survivorship-free US equities
   foundation: universe, index membership, delisted names, sectors, liquidity, returns.
2. **EODHD Fundamentals Data Feed** ($59.99/mo) — EOD history, fundamentals, calendar, splits /
   dividends, exchange lists, news / sentiment.

Phase 10-B therefore mines **only the data the user actually pays for** (Norgate + EODHD), audits the
real EODHD entitlements, normalizes every point-in-time-usable EODHD family, joins onto the Norgate
survivorship-free earnings-event panel, and runs the full Phase 8-X strong-alpha gate + a
1/5/21/63-day horizon sweep. **FMP may be present but is ignored as a research source.** No new paid
data is recommended until EODHD + Norgate are fully audited and tested.

## Files

- Runner: `research/run_phase10b_eodhd_norgate_exhaustive_alpha_factory.py`
- Tests: `tests/test_phase10b_eodhd_norgate_exhaustive_alpha_factory.py`
- Output: `research/output/phase10b_eodhd_norgate_exhaustive_alpha_factory/` (32 artifacts)
- Raw + normalized EODHD payloads: `research/data/eodhd/{raw,normalized}/…` (force-gitignored)

## Reuse (single source of truth — nothing reimplemented)

| Alias | Module | Reused for |
|-------|--------|-----------|
| `r8` | `run_phase8r_broad_bundle_evaluation` | EODHD host allow-list, URL redaction, key presence, fundamentals client |
| `s8` | `run_phase8s_autonomous_eodhd_alpha_factory` | EODHD data layer, `FWD_WINDOWS`, IO helpers |
| `w8` | `run_phase8w_expanded_universe_failure_attribution` | Norgate expanded event panel, cohort tag, liquidity proxy |
| `x8` | `run_phase8x_autonomous_strong_alpha_discovery` | broad strong gate, scenario/model scoring, scoreboards |
| `z8` | `run_phase8z_autonomous_no_excuses_alpha_agent` | point-in-time feature factory + hypotheses |
| `y8` | `run_phase8y_orthogonal_data_family_acquisition` | PIT status, as-of attach, gitignore helper |
| `c9` | `run_phase9c_verified_owned_feed_alpha_acquisition` | Norgate foundation verification |

## What it does (run order)

0. **Key-visibility preflight** (always first): EODHD must be `PRESENT`; FMP is recorded
   context-only and ignored; prints/writes `PRESENT`/`missing` only, never a key value.
1. **Norgate foundation** verification (reuse 9-C; reuse-vs-rebuild, pure read).
2. **Norgate survivorship-free earnings-event panel** + cohort tag + liquidity proxy (8-W/8-S):
   545 tickers / ~38,725 events with `fwd_exc_{1,5,21,63}`.
3. **EODHD entitlement audit**: one bounded probe per endpoint (fundamentals, eod, calendar
   earnings/trends, news, sentiments, insider-transactions, dividends, splits, macro, index
   constituents, exchange list, bulk-fundamentals, options). A block never stops the sweep; the
   exact HTTP status + entitlement class is recorded.
4. **Section + field inventory** from a representative cached fundamentals payload: every section,
   field, history depth, date column, and PIT-usable vs snapshot-only classification.
5. **Bounded, resumable EODHD acquisition** (cached, gitignored, skip-existing): single-symbol
   `fundamentals` (the workhorse), `sentiments`, `dividends`. Shared request budget, cycle loop.
6. **Point-in-time normalization** of each EODHD family into `(ticker, available_date, <feature>)`;
   records with no availability date / no value / an availability date after `as_of` are dropped.
7. **EODHD feature factory** (8-Z): each family explodes into ~21 PIT-safe transforms
   (level / chg / accel / lag / rolling mean-std-chg over 5/21/63 obs / within-month z /
   sector-neutral z / rank / winsor / × surprise|quality|value|momentum) + cross-family
   interactions.
8. **Broad strong gate** (8-X): IC t ≥ 3.0, BH-significant, net-of-25 bps positive (report 50 bps),
   both old/new cohorts positive, both pre/post-2020 halves positive, sector-diversified, ≥ 500
   tickers / ≥ 30,000 events. **No weak/constrained signal is promoted as strong.**
9. **Horizon sweep** 1/5/21/63-day within-month Spearman IC + t-stat (local; no existing-phase
   change). **Keep/upgrade/cancel** decision + **missing-data-after** + terminal decision.

## EODHD point-in-time feature families

PIT-usable, normalized from the cached fundamentals / news / dividends. The families marked
**additive** are the ones Phase 8-X had *not* exhausted:

| Family | Source / PIT date | Additive |
|--------|-------------------|----------|
| `eodhd_earnings_surprise` | Earnings.History.reportDate | re-test |
| `eodhd_eps_growth_yoy` | Earnings.History.reportDate | re-test |
| `eodhd_revenue_growth_yoy` | Income_Statement.quarterly.filing_date | re-test |
| `eodhd_gross_profitability` | Income/Balance.filing_date (Novy-Marx GP/assets) | **yes** |
| `eodhd_operating_accruals` | Income/CashFlow/Balance.filing_date (Sloan) | **yes** |
| `eodhd_asset_growth` | Balance_Sheet.quarterly.filing_date | **yes** |
| `eodhd_net_share_issuance` | Balance_Sheet.quarterly.filing_date | **yes** |
| `eodhd_leverage_change` | Balance_Sheet.quarterly.filing_date | **yes** |
| `eodhd_fcf_to_assets` | CashFlow/Balance.filing_date | **yes** |
| `eodhd_dividend_growth` | Dividends.declarationDate | **yes** |
| `eodhd_news_sentiment` | News.date (reuses the PIT-normalized table) | re-test |

### Snapshot-only sections (recorded, NEVER used for historical alpha)

`General`, `Highlights`, `Valuation`, `SharesStats` (incl. current short interest),
`Technicals`, `SplitsDividends`, `AnalystRatings` (incl. `TargetPrice` / `WallStreetTargetPrice`),
`Holders`, `InsiderTransactions`, `ESGScores`, `Earnings::Trend`, `Earnings::Annual`. These are
current-value snapshots with no usable dated history; using them in a historical backtest would
leak. They are inventoried in `eodhd_snapshot_only_fields.csv` but never fed to the factory.

## Point-in-time discipline

- Every normalized record carries an `available_date`; the as-of join is backward only
  (`available_date <= entry_date`).
- Fundamentals rows without a `filing_date` use the fiscal-period end + a conservative 90-day
  availability lag (no row is ever treated as available before it could have been filed).
- Future-dated records (`available_date > as_of`) are dropped at normalization.
- `fwd_exc_{1,5,21,63}` are computed strictly after `entry_date` (8-S forward-return engine).

## Allowed terminal decisions

`STRONG_ALPHA_FOUND_READY_FOR_REVIEW`, `EODHD_NORGATE_READY_FOR_NEXT_BATCH`,
`EODHD_NORGATE_EXHAUSTED_NO_STRONG_ALPHA`, `EODHD_ENTITLEMENT_LIMITATION_WITH_EXACT_FIX`,
`EODHD_USEFUL_BUT_OPTIONS_ADDON_RECOMMENDED`, `EODHD_USEFUL_BUT_ESTIMATE_TARGET_PROVIDER_NEEDED`,
`EODHD_NOT_SUFFICIENT_CANCEL_OR_DOWNGRADE`, `HARD_BLOCKER_REQUIRES_USER_ACTION`,
`ERROR_WITH_REPRO_COMMAND`. Forbidden: `MISSING_KEY`, `NO_DATA`, `EMPTY_PAYLOAD`, `NEEDS_PROVIDER`,
generic `ERROR`, or any decision that recommends new paid data before EODHD/Norgate are fully tested.

## Required artifacts (32)

`phase10b_eodhd_norgate_exhaustive_alpha_factory.json`, `key_visibility_preflight.csv`,
`eodhd_entitlement_audit.csv`, `eodhd_section_inventory.csv`, `eodhd_field_inventory.csv`,
`eodhd_snapshot_only_fields.csv`, `eodhd_pit_usable_fields.csv`, `acquisition_progress.csv`,
`raw_payload_manifest.csv`, `normalized_payload_manifest.csv`, `pit_normalization_audit.csv`,
`point_in_time_join_audit.csv`, `norgate_foundation_manifest.csv`, `feature_catalog.csv`,
`feature_coverage_report.csv`, `scenario_registry.csv`, `scenario_scoreboard.csv`,
`model_registry.csv`, `model_scoreboard.csv`, `horizon_sweep_report.csv`,
`strong_alpha_candidates.csv`, `rejected_hypotheses.csv`, `transaction_cost_report.csv`,
`cohort_stability_report.csv`, `subperiod_stability_report.csv`, `sector_concentration_report.csv`,
`leakage_audit.csv`, `eodhd_keep_upgrade_cancel_decision.csv`,
`missing_data_after_eodhd_norgate.csv`, `exact_next_commands.csv`, `phase10c_next_plan.json`,
`secret_safety_audit.csv`.

## Run

```powershell
# offline component/integration tests (no real key, no network)
$env:PAPER_TRADER_TEST_DATABASE_URL = "postgresql+psycopg2://postgres:Adam2015@localhost:5432/paper_trader_test"
python -m py_compile research/run_phase10b_eodhd_norgate_exhaustive_alpha_factory.py
python -m pytest tests/test_phase10b_eodhd_norgate_exhaustive_alpha_factory.py -q

# live (EODHD key read from env, never written; raw payloads gitignored)
python research/run_phase10b_eodhd_norgate_exhaustive_alpha_factory.py --live
```

## Constraints honored

Windows-compatible Python (stdlib + already-installed pandas/numpy); no package install; no Paper
Trader / GCP / orders / automation / deploy; no full regression (targeted tests only); EODHD key
never printed or written; raw + normalized EODHD payloads force-gitignored. **No commit. No push.**

## Status

**Live run completed 2026-06-28** (exit 0, single pass, ~2h08m wall, EODHD key `PRESENT`, raw payloads
gitignored, no key printed/written, no commit/push).

- **Runner terminal decision:** `STRONG_ALPHA_FOUND_READY_FOR_REVIEW` — the gate promoted **1** candidate:
  `f_accel_sn` over the **eodhd_eps_growth_yoy** family (sector-neutral *acceleration* of EPS-growth-YoY),
  ic_t = 3.91, BH-significant, net-of-25bps +0.0042, both cohorts +, both pre/post-2020 halves +,
  top-sector share 0.498 (just under the 0.5 diversification bar). `n_rejected` = 582, `n_constrained` = 0.
- **Entitlements:** 12 EODHD sections `ACCESS_VERIFIED` (200); `bulk_fundamentals` 403 (entitlement-blocked,
  expected — single-symbol is the workhorse); `options` 404 (not in the Fundamentals feed). 11 PIT families
  normalized (52k–514k rows each); feature coverage 29k–38.6k events of 38,725.
- **Keep/cancel:** EODHD **KEEP** ($59.99/mo), Norgate **KEEP** (expires 2026-12-24). Leakage audit all-PASS.
- **Analyst caveat (do NOT productize as-is):** the promoted signal passes the *letter* of the 8-X gate but is
  economically fragile on four independent axes — (1) new-cohort IC 0.0054 vs old 0.0654 (~12× decay);
  (2) ~50% single-sector concentration; (3) net-of-50bps spread is **negative** (−0.0008); (4) the **base**
  eps_growth_yoy signal is insignificant at every horizon (max t 1.56 @63d), so the t=3.91 lives only in one
  nonlinear transform at the 21-day horizon — the signature of an overfit transform, and it is the 1-of-11
  family winner of an identical transform whose cross-family t-stats scatter around zero. The horizon sweep
  instead surfaces two *economically coherent, sign-stable* base patterns the 21d gate rejected:
  **fcf_to_assets** (t 3.06/2.56/1.52/2.54, + every horizon) and **operating_accruals** (t −1.5/−2.4/−0.8/−3.1,
  correctly-signed Sloan). Recommend human review → a focused, properly out-of-sample follow-up on those two
  before any paper productization. **No commit. No push.** Targeted tests: 18 passed.
