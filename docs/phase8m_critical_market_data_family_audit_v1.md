# Phase 8-M — Critical Market-Data Family Entitlement Audit and Agent Controller Fix

**Status:** built, tested (offline). No commit. No push. Preview/research only.
**Files:** `research/run_phase8m_critical_market_data_family_audit.py`,
`tests/test_phase8m_critical_market_data_family_audit.py`, this doc.
**Output dir:** `research/output/phase8m_critical_market_data_family_audit/` (committed-safe).
**Paid-data dir:** `research/data/fmp/{raw,normalized}/` (gitignored — never committed).

---

## 1. Why this phase exists — the controller bug

The previous FMP live sample ran with a generic `--max-endpoints 6` cap. The FMP endpoint
catalog (`research/providers/fmp_client.py`) orders **fundamentals first** (`smoke_priority`
1–6) and the **critical earnings/analyst** endpoints **last** (`smoke_priority` 90–94). The
selector `_select_live_endpoints()` in `research/run_phase5e0_fmp_provider_trial_collector.py`
sorts by `smoke_priority` then slices `[:max_endpoints]`. Result: with a cap of 6, the six
most important missing data families were **never attempted** — the quality report showed
`0 planned / 0 attempted` for earnings + analyst, so **no upgrade decision could be made**.

That is a **controller bug**, not a data limitation. Phase 8-M fixes the controller and then
runs a bounded entitlement probe against exactly those critical families.

## 2. The fix — critical-first, cap-exempt probe queue

`build_probe_queue()` in the Phase 8-M script:

1. **Critical-first ordering.** Earnings/analyst probe endpoints carry ranks 0–5; everything
   else (key-metrics, ratios, statements, profile) ranks ≥ 10. The queue is the exact
   **inverse** of the catalog `smoke_priority` that caused the failure.
2. **A cap can only trim the non-critical tail.** If the endpoint-family cap is below the
   number of critical families, the cap is **overridden** (with a logged note) so no critical
   family is ever dropped. (Tested at caps 0/1/3/5 — all six criticals survive every time.)
3. **The request budget trims non-critical families only.** `families × tickers ≤ max_requests`
   is enforced by dropping the lowest-priority non-critical family, never a critical one.

The before/after ordering and the root-cause file/line are recorded in
`agent_controller_fix_report.csv`.

## 3. Bounded live limits (hard caps, never exceeded)

| Limit | Value |
|---|---|
| tickers | AAPL, MSFT, NVDA (max 3) |
| endpoint families | ≤ 12 |
| requests | ≤ 40 (12 families × 3 tickers = 36) |
| order | earnings/analyst **first** |

Default run is **dry-run** (no network). A live probe needs explicit `--live` **and** a present
`FMP_API_KEY`. Tests drive the probe through an injected `transport` — no key, no network.

## 4. The 20 mandatory data families and how each resolves

FMP-probed (10): `broad_earnings_surprise`, `earnings_calendar`, `analyst_estimates`,
`analyst_recommendations`, `analyst_price_targets`, `ratings_or_grades`, `key_metrics`,
`ratios`, `fundamentals_statements`, `company_profile`.

Resolved without an FMP probe (10):

| Family | Status | Source |
|---|---|---|
| `transcripts_or_guidance` | `NOT_TESTED_NO_MAPPING` | add `/stable/earning-call-transcript` mapping, re-probe |
| `news_or_press_releases` | `FREE_SOURCE_AVAILABLE` | GDELT (free) before any paid news |
| `insider_transactions` | `FREE_SOURCE_AVAILABLE` | SEC EDGAR Form 4 |
| `institutional_ownership_13f` | `FREE_SOURCE_AVAILABLE` | SEC EDGAR 13F |
| `options_iv_skew_putcall` | `NOT_AVAILABLE_IN_CURRENT_PROVIDER` | Intrinio / ORATS / Tradier / CBOE |
| `short_interest_borrow` | `FREE_SOURCE_AVAILABLE` | FINRA (free) before paid borrow |
| `sec_filings_event_classification` | `FREE_SOURCE_AVAILABLE` | SEC EDGAR |
| `macro_cross_asset_context` | `LOCAL_DATA_ALREADY_AVAILABLE` | Norgate (local) |
| `sector_industry_context` | `LOCAL_DATA_ALREADY_AVAILABLE` | Norgate (local) + FMP profile |
| `liquidity_volume_volatility_positioning` | `LOCAL_DATA_ALREADY_AVAILABLE` | Norgate (local) |

## 5. The nine terminal statuses and the per-endpoint classifier

`ACCESS_VERIFIED` · `EMPTY_BUT_ENDPOINT_REACHABLE` · `CLIENT_ENDPOINT_UPDATE_REQUIRED` ·
`SUBSCRIPTION_ENTITLEMENT_BLOCK` · `RATE_LIMITED` · `NOT_AVAILABLE_IN_CURRENT_PROVIDER` ·
`NOT_TESTED_NO_MAPPING` · `LOCAL_DATA_ALREADY_AVAILABLE` · `FREE_SOURCE_AVAILABLE`.

Classification reasons only from the HTTP status + payload shape (never the key):

| Result | likely_cause | status |
|---|---|---|
| 200 + rows | `accessible` | `ACCESS_VERIFIED` |
| 200 + empty | `no_data_for_symbol` | `EMPTY_BUT_ENDPOINT_REACHABLE` |
| 401 | `invalid_key` | `SUBSCRIPTION_ENTITLEMENT_BLOCK` (+ run-level key-rejected) |
| 402 / 403 | `subscription_entitlement_block` | `SUBSCRIPTION_ENTITLEMENT_BLOCK` |
| 404 | `wrong_endpoint_path` | `CLIENT_ENDPOINT_UPDATE_REQUIRED` |
| 429 | `rate_limit` | `RATE_LIMITED` |
| 5xx / network | `unknown` | `CLIENT_ENDPOINT_UPDATE_REQUIRED` (retry) |

In **dry-run** the FMP-probe families carry the non-terminal planning marker
`PENDING_LIVE_PROBE`; the live (or simulated) audit replaces it with a terminal status.

## 6. Research-director decision (one of seven)

`CURRENT_FMP_ACCESS_SUFFICIENT_FOR_CRITICAL_FAMILIES` · `FMP_SUBSCRIPTION_UPGRADE_REQUIRED` ·
`FMP_CLIENT_ENDPOINT_UPDATE_REQUIRED` · `CHEAPER_PROVIDER_RECOMMENDED` ·
`MIXED_PROVIDER_STRATEGY_RECOMMENDED` · `BLOCKED_MISSING_FMP_KEY` · `ERROR`.

**FMP is the recommended first provider** — one key unlocks the most critical families
(earnings surprises + calendar + analyst estimates/recommendations/price targets + grades).
Free sources are exhausted first (FINRA short interest, GDELT news, SEC EDGAR filings, Norgate
local). Options/news/short-interest specialists are on the **do-not-buy-yet** list until a
signal needs them. Costs are `UNKNOWN` wherever they cannot be verified from local code/catalog
(no invented dollar figures).

## 7. The 17 committed-safe artifacts

`phase8m_critical_market_data_family_audit.json`, `market_data_family_inventory.csv`,
`missing_data_family_priority_queue.csv`, `fmp_critical_endpoint_probe_plan.csv`,
`fmp_critical_endpoint_probe_results.csv`, `fmp_endpoint_failure_diagnosis.csv`,
`fmp_subscription_requirement_decision.csv`, `fmp_client_endpoint_gap_report.csv`,
`provider_alternative_matrix.csv`, `cheapest_viable_provider_matrix.csv`,
`data_family_to_signal_unlock_map.csv`, `provider_activation_commands.ps1` (placeholder only),
`paid_data_storage_manifest.csv`, `secret_safety_audit.csv`, `agent_controller_fix_report.csv`,
`research_director_decision.json`, `phase8n_next_plan.json`.

## 8. Secret discipline

`FMP_API_KEY` is read only from the environment, never printed, never written to disk.
Persisted URLs strip the key parameter entirely (placeholder only); no artifact contains the
literal `apikey=` marker. A leak scan over every written artifact backs `secret_safety_audit.csv`.
Raw + normalized paid payloads live only under the gitignored `research/data/fmp/` tree.

## 9. How to run

```powershell
# Default — dry-run plan + inventory, no network:
python research/run_phase8m_critical_market_data_family_audit.py

# Bounded live entitlement probe (FMP_API_KEY present in this session only):
python research/run_phase8m_critical_market_data_family_audit.py --live

# Tests (offline; no key, no network):
python -m pytest tests/test_phase8m_critical_market_data_family_audit.py -q
```

## 10. Constraints honored

Existing installed packages only (stdlib). No package install. No full S&P 500 backfill.
Bounded live limits. No Paper Trader. No GCP. No deploy. No broker/order/automation logic.
No weights optimized, no signs flipped. No failed experiments hidden. No commit. No push.
