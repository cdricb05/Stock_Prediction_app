# Phase 8-O - Cheapest Viable Provider Selection and Alternative-Data Entitlement Audit

Status: tested (offline, 21/21) - decision artifact regenerated. NOT committed.

## Why this phase exists

Phase 8-N patched the FMP controller (a 402 subscription block no longer stops the batch)
and then ran **live** with the user's `FMP_API_KEY`. The result was decisive: the **current
FMP plan is INSUFFICIENT** for broad signal research. All six critical families came back
`PARTIAL` - only **8/20** tickers covered each, with **~64-75%** of attempted cells
`402/403` subscription-blocked. Phase 8-N's own decision was `FMP_PLAN_COVERAGE_INSUFFICIENT`
and its upgrade decision was `UPGRADE_NOT_RECOMMENDED_USE_ALTERNATIVE`.

The bottleneck is now the **data source**, not the controller. Phase 8-O answers exactly one
question, cheaply and decisively: *which data-source path gives enough coverage for the
critical alpha families - especially broad earnings surprises/calendar and analyst
revisions/recommendations - at the lowest cost?* It tells the user exactly whether to use
Alpha Vantage, Finnhub, EODHD, an FMP upgrade, or a mixed-provider strategy.

This phase does **not** build more daemon infrastructure, run any full backfill, or default
to an FMP Ultimate upgrade. It reads Phase 8-N, compares providers, inventories which keys
are present, optionally probes only the keyed providers, and emits one honest recommendation.

## What it reads (Phase 8-N artifacts, read-only)

- `fmp_family_entitlement_matrix.csv` - per-family coverage, entitlement, block fraction,
  `broadly_blocked`.
- `fmp_subscription_block_pattern_report.csv` - per-family block pattern.
- `fmp_provider_upgrade_decision.csv` - the `__overall__` upgrade verdict.
- `fmp_cheaper_provider_alternative_report.csv` - Phase 8-N's per-family alternatives.
- `phase8n_fmp_critical_data_backfill_signal_expansion.json` - coverage counts, entitlement,
  block fractions, `missing_blocked_families`, `min_tickers_to_score`, decision.

The reader is tolerant of a missing/offline-regenerated set (it falls back to deriving the
blocked families from the entitlement matrix) and records `phase8n_found`.

## Committed-safe artifacts (13)

| Artifact | Contents |
| --- | --- |
| `phase8o_cheapest_provider_selection.json` | main report: blocked families, key inventory, strategy, decision, recommendation, leak-scan result |
| `phase8n_fmp_coverage_summary.csv` | per critical family: entitlement, coverage, block fraction, pattern, broadly_blocked |
| `critical_family_blocker_summary.csv` | which families are blocked, why, and the recommended unlock provider |
| `provider_decision_matrix.csv` | provider x family: env var, key_present, expected_coverage, cost tier, historical, point-in-time, sufficient history, endpoint mapping status, recommended_for_family |
| `cheapest_viable_provider_ranking.csv` | cheapest-first try order across the blocked families (AV -> Finnhub -> EODHD -> FMP upgrade) |
| `provider_key_inventory.csv` | per env var: provider, key_present (PRESENCE ONLY), value_read=False, families served |
| `provider_probe_plan.csv` | per keyed provider: representative endpoint (redacted), probe_enabled gate |
| `provider_probe_results.csv` | probe outcome per provider (NOT_PROBED_NO_KEY offline; PROBED_* when keyed) |
| `provider_cost_value_report.csv` | per provider: approx monthly cost, families covered, value note |
| `provider_env_var_setup_commands.ps1` | PLACEHOLDER PowerShell only - never a real key |
| `provider_acquisition_decision.json` | structured final recommendation (cheapest first, second, upgrade justified?, Ultimate rejected, env vars, next command) |
| `signal_family_unlock_map.csv` | per signal: required family, blocked_on_fmp, unlock provider, key present, unlock status |
| `phase8p_next_plan.json` | the next phase (alternative-provider controlled backfill) |

All artifacts carry **metadata only** - never a payload, never a key.

## Providers compared

| Provider | Env var | Cost | Role |
| --- | --- | --- | --- |
| FMP (current plan) | `FMP_API_KEY` | held | Phase 8-N proved PARTIAL on every critical family |
| FMP (upgrade tier) | `FMP_API_KEY` | ~$69/mo Premium | last-resort fallback; **Ultimate NOT justified** |
| Alpha Vantage | `ALPHAVANTAGE_API_KEY` | free (25/day) or ~$50 | `EARNINGS` / `EARNINGS_CALENDAR` - cheapest earnings route |
| Finnhub | `FINNHUB_API_KEY` | free (60/min) or ~$50 | recommendation trends / price targets - cheapest analyst route |
| EODHD | `EODHD_API_KEY` | ~$20-80/mo | single mid-priced bundle alternative |

Free sources (SEC EDGAR / FINRA / GDELT) do not serve the earnings-surprise / analyst
families and are out of scope for this decision.

## How the recommendation is derived

1. The six blocked families split into **earnings** (`earnings_surprises`,
   `earnings_calendar`) and **analyst** (`analyst_estimates`, `analyst_recommendations`,
   `analyst_price_targets`, `ratings_grades_consensus`).
2. Earnings -> **Alpha Vantage** (free `EARNINGS` gives quarterly EPS actual/estimate/surprise
   with report dates -> point-in-time safe). Analyst -> **Finnhub** (free recommendation
   trends; price targets/estimates partly premium).
3. Because no single free provider solves all six, the strategic recommendation is
   **`MIXED_PROVIDER_STRATEGY_REQUIRED`**, and the cheapest provider to try **first** is
   **Alpha Vantage** (the marquee earnings-surprise signal, free), then **Finnhub**.
4. **Env-key gate.** If none of the recommended alternative-provider keys is present in the
   environment, the terminal decision is **`BLOCKED_MISSING_ALTERNATIVE_PROVIDER_KEY`** - but
   the cheapest next provider is still named with its exact env var. If the keys are present,
   the decision is the strategy itself and the keyed providers can be probed with `--live`.
5. **FMP upgrade** is recommended only if a blocked family has *no* cheaper provider (never
   the case here). **FMP Ultimate is explicitly rejected** - no evidence requires it.

### Decision values

`ALPHA_VANTAGE_FIRST`, `FINNHUB_FIRST`, `EODHD_FIRST`, `FMP_UPGRADE_REQUIRED`,
`MIXED_PROVIDER_STRATEGY_REQUIRED`, `BLOCKED_MISSING_ALTERNATIVE_PROVIDER_KEY`,
`NO_PROVIDER_CAN_SOLVE_CHEAPLY`, `ERROR`. (The non-terminal `FMP_SUFFICIENT_NO_ALTERNATIVE_NEEDED`
sentinel is used only if Phase 8-N reported no blocked family; it is not one of the eight.)

## Current decision (offline, against the live Phase 8-N artifacts on disk)

- blocked families: **all six** critical families (`PARTIAL`, 8/20).
- recommended strategy: **`MIXED_PROVIDER_STRATEGY_REQUIRED`**.
- cheapest provider to try first: **Alpha Vantage** (`ALPHAVANTAGE_API_KEY`); second:
  **Finnhub** (`FINNHUB_API_KEY`).
- FMP upgrade justified: **No**. FMP Ultimate rejected: **Yes**.
- key inventory in this shell: no alternative key present -> decision
  **`BLOCKED_MISSING_ALTERNATIVE_PROVIDER_KEY`** (set `ALPHAVANTAGE_API_KEY` first).

## Secret discipline (hard rules)

- Provider keys are read **only** from their env vars, never printed, never written to disk.
  The key inventory records **presence only** (`value_read=False`).
- Every persisted URL strips the secret query parameter (`apikey` / `token` / `api_token`)
  entirely and appends a placeholder; no committed artifact contains `apikey=`. A leak scan
  over the written artifacts confirms `secret_safety_leak_scan_clean`.
- Default run is **offline** (no network). A probe needs `--live` **and** that provider's key.
  Tests inject a `transport` and never touch a key or the network.
- Any probe payload is persisted only under `research/data/<provider>/{raw,normalized}/`,
  which the phase **force-gitignores** (belt-and-braces `*` + `!.gitignore`, mirroring
  `research/data/fmp/.gitignore`) before writing - so paid data can never be staged.

## Run

```powershell
# Offline decision (default; reads Phase 8-N, no network):
python research/run_phase8o_cheapest_provider_selection.py

# Bounded live probe of the keyed alternative providers (cheapest first):
$env:ALPHAVANTAGE_API_KEY = '<PASTE_ALPHAVANTAGE_KEY_HERE>'
python research/run_phase8o_cheapest_provider_selection.py --live
# then, for the analyst families:
$env:FINNHUB_API_KEY = '<PASTE_FINNHUB_KEY_HERE>'
python research/run_phase8o_cheapest_provider_selection.py --live

# Test (fully offline):
python -m pytest tests/test_phase8o_cheapest_provider_selection.py -q
```

## Constraints honored

Existing installed packages only (stdlib). No package install. No full S&P 500 backfill (a
probe is at most one ticker x one endpoint per keyed provider). No API key printed or written.
No raw/normalized paid data committed. No Paper Trader, no GCP, no deploy, no broker / order /
automation logic. FMP Ultimate never recommended by default. No commit. No push.
