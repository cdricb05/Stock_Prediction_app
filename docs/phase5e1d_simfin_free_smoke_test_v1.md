# Phase 5-E1D — SimFin Free Live Smoke Test (v2)

## Correction (v2): bulk package, not per-ticker web requests

v1 of this smoke used a **custom per-ticker web request** path (one HTTPS request per
ticker per statement against the SimFin v3 web endpoints). In a live run it
authenticated and the **Companies** lookup worked, but **every** financial-statement
lookup (Income Statement, Balance Sheet, Cash Flow, Derived, and the bank variants)
came back **HTTP 500 / 429**. That does **not** prove SimFin Free lacks quarterly
fundamentals — it proves the **per-ticker web path was the wrong access method**.

SimFin documents the **official Python package / bulk dataset download** as the
preferred mechanism: download a whole dataset **once**, then filter the tickers you
care about **locally**. v2 switches the live path to that bulk workflow and **removes**
the deprecated per-ticker web code entirely. The report now records
`access_method = official_simfin_package_or_bulk`, `web_api_per_ticker_deprecated =
true`, and `live_web_api_result = rejected_due_500_429`.

## Why this phase exists

Phase 5-E1C dropped FMP (its premium fundamentals tier needs a ~$588 **annual
upfront** payment) and selected **SimFin** as the preferred low-cost provider:
standardized quarterly statements, a genuine **free tier**, an optional **monthly**
SimFin+ subscription, and no annual lock-in.

The user then created a **free** SimFin account (User ID `cdricb05`) and holds a
free-tier API key. SimFin's account page states the **free datasets contain ~5
years of history and are delayed by ~12 months**. That is fine for research /
backtesting, but **not** for live production signals.

Phase 5-E1D is a **smoke test, not a collector and not a model**. Before building
anything, it answers one question: *can SimFin Free actually deliver the quarterly
fundamentals we need — for both standard companies and banks?*

This is **Track A quant work**. It touches no Paper Trader file, no GCP config, no
deploy path, no orders / automation, no D: drive, and installs nothing.

## What it deliberately is (and is NOT)

- **Dry-run first.** With no flags it requires **no API key**, performs **no
  network calls** (the `urllib` path is only reachable under `--live`), and writes
  only committed-safe planning artifacts. The recommendation is
  `READY_FOR_SIMFIN_FREE_LIVE_SMOKE`.
- **Opt-in live smoke.** `--live` makes **minimal** verified calls using
  `SIMFIN_API_KEY` (read from the environment **only**), writing raw + normalized
  probes to a **git-ignored** local tree and summaries to `research/output/`.
- **Not a collector.** It probes dataset access / schema / coverage for 10 tickers;
  it does not build the full fundamentals pipeline. That is Phase 5-E1E, whose plan
  this phase emits (`phase5e1e_simfin_collector_plan.json`).
- **Never live-trading-ready.** The free tier's ~12-month delay means
  `usable_for_live_trading_today` is always **false**; the data is only
  `point_in_time_safe_for_research`.

## Test tickers (standard vs banks handled separately)

Banks use SimFin's **bank statement templates**, which differ line-item-by-line-item
from standard companies, so they are tracked separately end-to-end (their own
datasets, request rows, probe rows, and coverage rows; `company_template = "banks"`).

- **Standard:** AAPL, MSFT, AMZN, NVDA, APH, ABT, ACN
- **Banks:** JPM, BAC, C

## Datasets verified

| Standard company | Bank |
|------------------|------|
| Companies | Companies |
| Income Statement | Income Statement (Banks) |
| Balance Sheet | Balance Sheet (Banks) |
| Cash Flow | Cash Flow (Banks) |
| Derived Figures & Ratios *(optional)* | Derived Figures & Ratios (Banks) *(optional)* |

The three required quarterly statements (income / balance / cash flow) must verify
for **both** a standard company and a bank for the smoke to clear into 5-E1E.

## Run it

**Dry-run (no key, no network):**
```powershell
python research\run_phase5e1d_simfin_free_smoke_test.py
```

**Live smoke (requires the free key in the environment AND the `simfin` package):**
```powershell
# one-time package install into this project's venv (NOT done by the runner):
C:\Users\binis\paper_trader\.venv-win\Scripts\python.exe -m pip install simfin

$env:SIMFIN_API_KEY = "<your-free-simfin-key>"
python research\run_phase5e1d_simfin_free_smoke_test.py --live --max-tickers 10
```

The key is read from `SIMFIN_API_KEY` only — never pasted into source, never
committed, never printed (only the boolean `api_key_present`). It is passed **only**
to the official package's `simfin.set_api_key()`; it never appears in a URL, log
line, or artifact.

## Access method & package policy

- **The only live access method is the official `simfin` package / bulk download**
  (`access_method = official_simfin_package_or_bulk`). Each dataset is downloaded
  **once** with `market='us'` and `variant='quarterly'`, then the 10 test tickers are
  filtered **locally** — never one request per ticker.
- The deprecated per-ticker web path has been **removed** from the module.
- **Package required.** If `simfin` is not installed, the runner does **not** install
  it and makes **no** download: it returns `BLOCKED_NEEDS_SIMFIN_PACKAGE` and prints
  the exact install command
  (`C:\Users\binis\paper_trader\.venv-win\Scripts\python.exe -m pip install simfin`).
- **Bank templates are discovered, not assumed.** The runner attempts dedicated
  `*_banks` loaders; if the package does not expose them, it verifies the bank tickers
  via the standard datasets and records `bank_template_separate = false`.

## Recommendations (the six allowed values)

| Value | When |
|-------|------|
| `READY_FOR_SIMFIN_FREE_LIVE_SMOKE` | dry-run completed; live smoke is ready |
| `READY_FOR_PHASE5E1E_SIMFIN_COLLECTOR` | live smoke verified quarterly IS/BS/CF for a standard company **and** a bank |
| `BLOCKED_NEEDS_SIMFIN_PACKAGE` | `bulk_package` chosen but `simfin` not installed |
| `BLOCKED_SIMFIN_FREE_NO_QUARTERLY_FUNDAMENTALS` | live calls succeeded but the free tier did not return the required quarterly fundamentals for both templates |
| `BLOCKED_MISSING_SIMFIN_KEY` | `--live` without `SIMFIN_API_KEY` |
| `USE_SEC_LOCAL_FALLBACK` | fall back to the free phase3g/3h SEC pipeline |

## Artifacts

**Committed-safe (summaries only — no payloads, no key) under
`research/output/phase5e1d_simfin_free_smoke_test/`:**

- `phase5e1d_simfin_free_smoke_test.json` — the full report (mode, key presence,
  free-tier facts, datasets planned/verified, ticker lists, file counts,
  `point_in_time_safe_for_research`, `usable_for_live_trading_today`,
  recommendation + next phase, safety contract).
- `simfin_dataset_access_plan.csv` — each dataset × template, required/optional,
  statement code, redacted endpoint.
- `simfin_smoke_request_plan.csv` — the minimal per-(ticker, dataset) request plan.
- `simfin_schema_probe_report.csv` — per request: `planned` in dry-run; in live the
  http status, schema status, column count, `quarterly_period_found`, fiscal years.
- `simfin_coverage_report.csv` — per ticker: required vs verified datasets, history.
- `simfin_point_in_time_readiness.csv` — records the **12-month** delay, the ~5-year
  history, the PIT alignment rule, and that live trading today is blocked.
- `simfin_secret_safety_audit.csv` — the key-handling and git-ignore checks.
- `simfin_package_probe_report.csv` — package present/version, access method, datasets
  attempted/loaded, rows loaded, columns detected, test tickers found, quarterly dates
  found, recommendation.
- `phase5e1e_simfin_collector_plan.json` — the next-phase collector design.

**Git-ignored local data under `research/data/simfin/` (never committed):**

- `raw/<template>/<ticker>/<code>.json` — verbatim SimFin responses (live only).
- `normalized/<template>/<ticker>/<code>.csv` — small flattened probes (live only).

`research/data/simfin/.gitignore` ignores `raw/`, `normalized/`, and `*` (allowing
only the `.gitignore` itself) — mirroring the Phase 5-E1 FMP convention so no
payload can ever be staged from there by accident.

## Closing answers

- **Can the free SimFin live smoke be run?** Yes — set `SIMFIN_API_KEY` and run
  `--live --max-tickers 10`. The dry-run already validated the plan and reports
  `READY_FOR_SIMFIN_FREE_LIVE_SMOKE`.
- **Is a package install needed?** Yes. The live bulk workflow requires the official
  `simfin` package, which is **not** installed in this venv. The runner never installs
  it; install it once with
  `C:\Users\binis\paper_trader\.venv-win\Scripts\python.exe -m pip install simfin`.
- **Exact live command** (after the package is installed):
  `python research\run_phase5e1d_simfin_free_smoke_test.py --live --max-tickers 10`
  (with `$env:SIMFIN_API_KEY` set first).
- **Is commit appropriate?** Not yet — per the task, do **not** commit or push. Only
  the four new files (runner, test, doc, data `.gitignore`) plus the committed-safe
  output artifacts would ever be committed; raw/normalized SimFin data must stay
  git-ignored.

## Safety contract

`preview_only=true`; `orders_enabled`, `automation_enabled`,
`broker_execution_enabled`, `production_replacement`, `writes_to_d_drive`,
`modifies_paper_trader`, `modifies_gcp`, `installs_packages`, `builds_collector` all
`false`. Dry-run is offline and key-less; live uses the env key only via the
`Authorization` header, writes raw/normalized only to the git-ignored data tree,
never prints/writes the key, and never deletes data. No FMP. No paid SimFin
subscription. No commit, no push.
