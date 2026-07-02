# Phase 10-A - Missing Alpha Data Direct Acquisition, PIT Normalization, and Alpha Search

Status: implemented + tested (fully-offline targeted tests) and **executed** against the real
environment with the visible market-data keys. Runner:
`research/run_phase10a_missing_alpha_data_acquisition.py`. Tests:
`tests/test_phase10a_missing_alpha_data_acquisition.py`. Artifacts:
`research/output/phase10a_missing_alpha_data_acquisition/`. Nothing committed, nothing pushed.

## Why this phase exists

Phase 9-C acquired and exhausted the OWNED sentiment / insider / recommendation feeds
(`OWNED_FEEDS_EXHAUSTED_NO_STRONG_ALPHA`, best candidate `f_lag1_sn` at t=1.9). The remaining,
un-mined alpha mechanisms are forward-looking / positioning families that need NEW provider data:

1. **analyst_estimate_revisions** - EPS / revenue estimate drift before the report
2. **price_target_revisions** - consensus target change vs price
3. **short_interest_days_to_cover** - crowding / squeeze risk, days-to-cover
4. **options_iv_skew_put_call** - forward-looking risk repricing

Phase 10-A directly probes + (where entitled) downloads each family from the currently visible keys,
normalizes every datum point-in-time, joins it onto the existing Phase 9-C / Norgate expanded
earnings-event panel (545 tickers / ~38,725 events), builds a broad feature catalogue, and runs the
SAME Phase 8-X broad strong-alpha gate **plus a 1/5/21/63-day horizon sweep**.

## Visible keys (preflight)

The phase's first action is a key-visibility preflight (PRESENT/missing only; values never read).
Required visible before any live acquisition: `EODHD_API_KEY`, `FINNHUB_API_KEY`, `FMP_API_KEY`,
`ALPHAVANTAGE_API_KEY`, `NASDAQ_DATA_LINK_API_KEY`, `POLYGON_API_KEY`, `TIINGO_API_KEY`,
`FRED_API_KEY`. `ORATS_API_KEY` / `INTRINIO_API_KEY` / `BENZINGA_API_KEY` are recorded **missing**
(the user does not have them) and are **never required** - they never block the phase. If a required
key is not visible, the phase stops with `KEY_ENV_NOT_VISIBLE_RESTART_CLAUDE_CODE`.

## Provider priority per family (brief's exact order)

| Family | Provider order (priority) | Missing-key specialists (recorded only) |
|--------|---------------------------|------------------------------------------|
| analyst_estimate_revisions | FMP -> Finnhub -> Alpha Vantage (EARNINGS) | Intrinio |
| price_target_revisions | FMP -> Finnhub -> Polygon (Benzinga add-on) | Benzinga, Intrinio |
| short_interest_days_to_cover | Nasdaq Data Link (FINRA) -> Polygon -> FMP | Intrinio |
| options_iv_skew_put_call | Polygon (chain snapshot) | ORATS, Intrinio |

A provider that blocks entitlement **never** stops the phase: every configured provider is probed,
the exact provider / endpoint (redacted) / HTTP status / blocker is recorded in
`provider_family_attempts.csv` + `entitlement_blockers.csv`, and the next accessible provider is
tried. FMP is probed like any other present key - entitlement is **measured, not assumed**.

## Reuse (single source of truth - nothing reimplemented)

- **8-O** (`o8`): key-presence (value never read), bounded host-allow-listed GET (`_live_get`),
  `ProbeError`, URL redaction, IO.
- **8-Y** (`y8`): provider spec (`_ep`), entitlement classifier (`classify_entitlement`),
  bounded `acquire_family`, PIT helpers (`_pit_status`, `_to_float`), `attach_orthogonal_feature`,
  the force-gitignore helper.
- **8-W** (`w8`): the expanded point-in-time earnings-event table (`build_expanded_ev`), the old/new
  cohort tag (`tag_cohort`), and the Norgate-derived liquidity proxy (`attach_liquidity_proxy`).
- **8-X** (`x8`): the broad strong-alpha gate (`_finalize_gates` / `classify`), the scenario / model
  scoring core (`evaluate_scenario`), Benjamini-Hochberg multiple-testing, the per-candidate
  scoreboard/report row builders.
- **8-Z** (`z8`): the point-in-time `feature_factory` (level / lag / rolling 5-21-63 / change /
  acceleration / z / sector-neutral z / rank / winsor / x surprise|quality|value|momentum) and
  `evaluate_factory_hypotheses`.
- **9-C** (`c9`): the Norgate foundation verification (reuse-vs-rebuild, pure read).
- `s8` = 8-S data layer (`FWD_WINDOWS = (1,5,21,63)`, forward returns) ; `t8` = 8-T scoring core.

## What 10-A adds on top

1. **Key-visibility preflight** (`key_visibility_preflight`) - PRESENT/missing for the 8 required +
   3 optional keys; the optional specialist keys never block.
2. **Missing-alpha family registry** - the 4 families with providers in the brief's priority order,
   plus the missing-key specialists (ORATS / Intrinio / Benzinga) mapped to the families they would
   unlock with the exact purchase action.
3. **Per-(family, provider) entitlement probe** (`probe_family_provider`) - bounded 1-ticker probe
   capturing the HTTP status / entitlement class; one blocked provider never stops the sweep; the
   persisted endpoint is redacted with a self-contained redactor robust to provider secret-param
   names not in the shared set (e.g. Nasdaq's `api_key`).
4. **Provider-aware PIT normalizers** (`_extract_family_records` + `normalize_family_pit`) - the real
   FMP / Finnhub / Alpha Vantage / Polygon / Nasdaq shapes for each family flattened into a uniform
   `(ticker, available_date, <feature>)` + family-specific fields schema. Every record is classified;
   records with no availability date, no value, or an availability date AFTER the as-of (future leak)
   are dropped.
5. **Cross-family interactions** (`build_cross_family_features`) - within-month z products across the
   new families + each family x earnings surprise / momentum / quality / value / liquidity.
6. **1/5/21/63-day horizon sweep** (`horizon_sweep`) - a pure local within-month Spearman-IC + t-stat
   of each family's primary level / z against `fwd_exc_{1,5,21,63}` (the 8-S forward-return columns).
   This is a local extension that does **not** modify the 8-X scoring core or any other phase.
7. **Bounded, resumable, ceiling-capped campaign** - `max_tickers` 545, `max_requests_per_run` 2000,
   `max_cycles` 5, `total_request_ceiling` 8000; skip-existing makes acquisition resumable.

## Terminal decisions (allowed)

`STRONG_ALPHA_FOUND_READY_FOR_REVIEW` | `MISSING_ALPHA_DATA_ACQUIRED_READY_FOR_NEXT_BATCH` |
`ACCESSIBLE_MISSING_ALPHA_DATA_EXHAUSTED_NO_STRONG_ALPHA` | `ALL_TARGET_FAMILIES_BLOCKED_BY_ENTITLEMENT` |
`PROVIDER_KEYS_REQUIRED_WITH_EXACT_ACTIONS` | `EXACT_PAID_PROVIDER_REQUIRED_TO_CONTINUE` |
`KEY_ENV_NOT_VISIBLE_RESTART_CLAUDE_CODE` | `HARD_BLOCKER_REQUIRES_USER_ACTION` |
`ERROR_WITH_REPRO_COMMAND`.

Every terminal carries an exact data family / provider / endpoint / next action (in
`exact_next_commands.csv` and `phase10b_next_plan.json`). Forbidden (never emitted): a bare
`MISSING_KEY` / `NO_DATA` / `EMPTY_PAYLOAD` / `NEEDS_PROVIDER` / generic `ERROR`. A
normalized-but-zero-coverage family is a date/ticker mismatch (or a snapshot-only endpoint) with an
exact deeper-history fix - **never** reported as exhaustion.

## Required artifacts (30)

`phase10a_missing_alpha_data_acquisition.json`, `key_visibility_preflight.csv`,
`provider_family_attempts.csv`, `entitlement_blockers.csv`, `missing_keys_exact_actions.csv`,
`provider_purchase_required.csv`, `acquisition_progress.csv`, `raw_payload_manifest.csv`,
`normalized_payload_manifest.csv`, `pit_normalization_audit.csv`, `point_in_time_join_audit.csv`,
`usable_missing_alpha_families.csv`, `unusable_missing_alpha_families.csv`,
`feature_coverage_report.csv`, `feature_catalog.csv`, `scenario_registry.csv`,
`scenario_scoreboard.csv`, `model_registry.csv`, `model_scoreboard.csv`, `horizon_sweep_report.csv`,
`strong_alpha_candidates.csv`, `rejected_hypotheses.csv`, `transaction_cost_report.csv`,
`cohort_stability_report.csv`, `subperiod_stability_report.csv`, `sector_concentration_report.csv`,
`leakage_audit.csv`, `exact_next_commands.csv`, `phase10b_next_plan.json`, `secret_safety_audit.csv`.

Raw + normalized provider payloads themselves stay force-gitignored under
`research/data/<provider>/` (only manifests / scoreboards / metadata are written to the output dir).

## Run

```powershell
# Probe + acquire live with the visible keys (read from env, never written) + run the gate + sweep:
python research/run_phase10a_missing_alpha_data_acquisition.py --live

# Resumable continuation / deeper history:
python research/run_phase10a_missing_alpha_data_acquisition.py --live --refresh `
    --max-tickers 545 --max-requests 2000 --max-cycles 5 --request-ceiling 8000

# Targeted tests (fully offline; injected transports; no key, no network):
python -m pytest tests/test_phase10a_missing_alpha_data_acquisition.py -q
```

Defaults: `--max-tickers 545`, `--max-requests 2000`, `--max-cycles 5`, `--request-ceiling 8000`,
`--deep-from 2016-01-01`, as-of `2026-06-26`.

## Constraints honored

Windows-compatible Python (stdlib + the already-installed pandas/numpy the 8-X stack uses); no package
install; no Paper Trader, no GCP, no orders, no automation, no deploy; no full Phase-8 regression
(targeted tests only); keys never printed or written; raw + normalized provider payloads
force-gitignored; provider raw/normalized data never deleted/reset. Weak / constrained signals are
never promoted. No commit. No push.

## Status (live run)

Populated from the real-environment run. See
`research/output/phase10a_missing_alpha_data_acquisition/phase10a_missing_alpha_data_acquisition.json`
for the authoritative numbers.

Real-environment run (`--live`, as-of 2026-06-26; all 8 required keys visible, preflight PASS):

- **Terminal decision:** `ACCESSIBLE_MISSING_ALPHA_DATA_EXHAUSTED_NO_STRONG_ALPHA`. No strong alpha.
- **Live entitlement reality (HTTP):**
  - `analyst_estimate_revisions`: FMP 403, Finnhub 403, **Alpha Vantage 200 (ACCESS_VERIFIED)**.
  - `price_target_revisions`: FMP 403, Finnhub 403, Polygon 403 -> **all entitlement-blocked**.
  - `short_interest_days_to_cover`: Nasdaq Data Link 403, **Polygon 200 (ACCESS_VERIFIED)**, FMP 403.
  - `options_iv_skew_put_call`: Polygon 403 -> **entitlement-blocked**.
- **Acquired (live, bounded, resumable):**
  - `short_interest_days_to_cover` via **Polygon - the FULL 545-ticker universe** (5,450 PIT rows /
    545 tickers; **17,496 covered events**). Fully tested.
  - `analyst_estimate_revisions` via **Alpha Vantage - rate-limit-capped at 23 tickers** (2,154 PIT
    rows / 23 tickers; 1,395 covered events; Alpha Vantage returned 1,044 `RATE_LIMITED` responses
    after its daily quota). Under-tested, NOT exhausted.
  - Total 1,572 requests (ceiling 8,000). Norgate `REUSE_EXISTING_PANEL` (last 2026-06-30).
- **Strong gate + horizon sweep:** 93 scenarios + 4 models over the broad 8-X gate; 1/5/21/63-day
  horizon sweep (16 rows / 4 signals). **strong=False**; best `f_rstd_63_sn` (short-interest 63-obs
  rolling-volatility, sector-neutral) at **t=1.56** - REJECTED_BELOW_GATE (t>=3.0). The discovery-pass
  best (`f_rstd_5_raw`, t=2.77 on 30 tickers) was small-sample noise: at full universe the best short
  -interest signal collapses to t=1.56.
- **Secret-safety:** leak scan clean. **Artifacts:** all 30 written. **Targeted tests:** 15/15 pass.
- **Next (exact):** (1) complete `analyst_estimate_revisions` - wait for the Alpha Vantage daily
  quota reset OR upgrade the AV tier / use a paid estimate feed (Intrinio/Zacks), then re-run
  `--live --refresh`; (2) `price_target_revisions` needs a paid feed (FMP Premium / Benzinga /
  Intrinio); (3) `options_iv_skew_put_call` needs a paid options feed (ORATS / Intrinio / Polygon
  Options Starter). The one fully-testable deep family (short interest) shows no strong alpha.
