# Phase 5-E1C — Low-Cost Fundamentals Provider Proof Test (v1)

## Why this phase exists

Phase 5-E1B got the FMP backfill working safely (controlled, resumable, secret-safe,
cache-protected). But we then discovered that the **FMP Premium tier the enriched panel
needs requires a ~$588 ANNUAL UPFRONT payment**. That is not acceptable for a research
experiment. So FMP is **dropped as the primary fundamentals provider** and kept only as a
**negative cost benchmark**.

Phase 5-E1C is a **proof test, not a collector**. Before building another full integration,
it answers one question, offline: *which is the cheapest usable provider for quarterly
fundamentals that we can validate without sales/contact friction and without an annual
lock-in?*

This is still **Track A quant work**. It touches no Paper Trader file, no GCP config, no
deploy path, no orders/automation, no D: drive, and installs nothing.

## What "proof test" means here (and what it deliberately is NOT)

- **No network.** The runner [`research/run_phase5e1c_provider_proof_test.py`](../research/run_phase5e1c_provider_proof_test.py)
  contains **zero network code** — no `urllib`, no `requests`, no sockets. "No live calls by
  default" is therefore structural, not a flag you could flip on by accident.
- **No API key.** Nothing reads or writes any `*_API_KEY`. A test deletes all provider key
  env vars and re-runs to prove the run still succeeds.
- **No collector yet.** It does not fetch, normalize, or store any fundamentals. It only
  scores providers against the Phase 5-E2 requirements and emits a recommendation.
- **Documented expectations, not verified facts.** Pricing and coverage come from vendor
  documentation knowledge and are stamped
  `pricing_source = vendor_documentation_knowledge_needs_user_confirmation`. The proof test
  verified **nothing** live, so every pricing/coverage value is a documented expectation to
  confirm during the free smoke test, not a measured result.

## Requirements a provider must satisfy

For the Phase 5-E2 enriched panel, the chosen provider must be able to supply, for the 10
large-cap test tickers (**AAPL, MSFT, AMZN, NVDA, JPM, BAC, C, APH, ABT, ACN** — all current
S&P 500 names):

- quarterly **income statement**
- quarterly **balance sheet**
- quarterly **cash flow**
- at least **5 years** of history (if available)

A provider **passes the proof gate** when it supports all three quarterly statements through
a **self-serve, no-annual-upfront** path (a free tier and/or a monthly option), with **no
sales contact required**, and offers an **API or bulk download**.

## Providers evaluated (priority order)

| # | Provider | Annual upfront | Monthly | Free tier | All 3 statements | Friction | Passes gate |
|---|----------|----------------|---------|-----------|------------------|----------|-------------|
| 1 | **SimFin** | No | Yes | Yes | Yes | low | ✅ |
| 2 | EODHD | No | Yes | Yes (demo token) | Yes | low–moderate | ✅ |
| 3 | Tiingo | No | Yes | Yes | Yes | moderate | ✅ (alternative only) |
| 4 | SEC EDGAR (local fallback) | No | n/a (free) | Yes | Yes | moderate | ✅ |
| — | **FMP** (negative benchmark) | **Yes (~$588)** | unknown | No | Yes | blocked_by_pricing | ❌ rejected |

Notes:
- **Tiingo** has no dedicated recommendation token in the allowed vocabulary; it is profiled
  as a self-serve *alternative* (credited only because it is usable without sales/contact
  friction) and ranked third behind SimFin/EODHD.
- **SEC EDGAR** is the free, key-less fallback. The XBRL→field normalization it needs is
  already prototyped in this repo
  ([`research/prototype_phase3g_sec_fundamentals_minipipeline.py`](../research/prototype_phase3g_sec_fundamentals_minipipeline.py),
  [`research/build_phase3h_sec_fundamental_features.py`](../research/build_phase3h_sec_fundamental_features.py)),
  which is why its friction is "moderate," not "high."
- **FMP** is retained only so the comparison has an explicit cost benchmark; it is marked
  `rejected_due_annual_upfront` and can never be the selected provider.

## Recommendation

**`TRY_SIMFIN_FREE_OR_MONTHLY`** — SimFin is the first provider in priority order that clears
the proof gate: standardized quarterly IS/BS/CF, a genuine **free tier** to validate all 10
tickers before paying, an optional **monthly** SimFin+ subscription (no annual lock-in), a
self-serve instant API key, and both REST and bulk CSV access.

The four allowed recommendation values are:

- `TRY_SIMFIN_FREE_OR_MONTHLY`
- `TRY_EODHD_MONTHLY`
- `USE_SEC_LOCAL_FALLBACK`
- `STOP_NO_PROVIDER_SELECTED`

The selection is **deterministic and data-driven**: it walks SimFin → EODHD → SEC and picks
the first that passes the gate, else `STOP_NO_PROVIDER_SELECTED`.

## Confirming or changing the facts without editing code

Because the proof test verified nothing live, you can inject **user-confirmed** pricing or
coverage via an optional JSON notes file and re-run:

```powershell
python research\run_phase5e1c_provider_proof_test.py --provider-notes my_notes.json
```

```json
{ "providers": { "simfin": { "annual_upfront_required": true } } }
```

The override is read-only and network-free. If confirmed facts disqualify SimFin, the
recommendation falls through deterministically (e.g. to `TRY_EODHD_MONTHLY`, then
`USE_SEC_LOCAL_FALLBACK`). Two tests prove exactly this fall-through behavior.

## Artifacts (committed — summaries only, no paid data, no key, no binaries)

- [`research/output/phase5e1c_provider_proof_test/provider_comparison.csv`](../research/output/phase5e1c_provider_proof_test/provider_comparison.csv)
  — one row per provider with the required fields (pricing model, `annual_upfront_required`,
  monthly option, data families, API/bulk availability, friction, next action) plus the
  derived proof-gate columns.
- [`research/output/phase5e1c_provider_proof_test/phase5e1c_provider_proof_test.json`](../research/output/phase5e1c_provider_proof_test/phase5e1c_provider_proof_test.json)
  — the full report: provider profiles, requirements, recommendation + reason, free-test /
  monthly-only flags, per-provider account setup, next action, and the safety contract.
- [`research/output/phase5e1c_provider_proof_test/test_ticker_requirements.csv`](../research/output/phase5e1c_provider_proof_test/test_ticker_requirements.csv)
  — the 10 test tickers × the required data families and history depth.

## Account / key setup per provider

- **SimFin** — sign up at simfin.com, generate an API key, store it only in a `SIMFIN_API_KEY`
  env var. The free tier needs no payment; SimFin+ is optional month-to-month.
- **EODHD** — register at eodhd.com, get the token, store it as `EODHD_API_KEY`. Demo token is
  free; the Fundamentals add-on is month-to-month.
- **Tiingo** — register at tiingo.com, get the token (`TIINGO_API_KEY`), enable the Power plan
  and accept the fundamentals add-on terms.
- **SEC EDGAR** — no account, no key, no payment; only a descriptive User-Agent header is
  requested by SEC.

Keys are **environment-only** in every case — never committed, never written to an artifact.

## Answers to the closing questions

- **Provider recommendation:** SimFin (`TRY_SIMFIN_FREE_OR_MONTHLY`).
- **Free test possible?** Yes — SimFin's free tier (and EODHD's demo token, and SEC's free
  data) let us validate the 10 tickers before paying anything.
- **Monthly-only payment possible?** Yes — SimFin+, EODHD, and Tiingo all bill month-to-month;
  none require an annual upfront.
- **Exact next step:** create a **free** SimFin account, generate its API key (env var only),
  and run a real 1-ticker free-tier smoke for **AAPL** (quarterly IS/BS/CF) to confirm schema
  and ≥5y history **before** building any collector or paying anything.

## Reproduce (Windows PowerShell only)

```powershell
Set-Location "C:\Users\binis\Stock_Prediction_app_push"
python research\run_phase5e1c_provider_proof_test.py
python -m pytest tests\test_phase5e1c_provider_proof_test.py -q
```

## Safety contract

Offline evaluation only. `preview_only=true`; `orders_enabled`, `automation_enabled`,
`broker_execution_enabled`, `production_replacement` all `false`. No network, no API key, no
package installs, no collector, no paid data, no binary artifacts, no writes to D:, no Paper
Trader changes, no GCP changes, no deploy, no commit. FMP is a negative benchmark only,
marked `rejected_due_annual_upfront`.
