# Phase 5-E1E — Bounded SimFin Free Fundamentals Collector (v1)

## Why this phase exists

Phase 5-E1D's live smoke proved — via the **official `simfin` package / bulk-download
workflow** — that SimFin Free returns quarterly income / balance / cash-flow statements
for both standard companies and banks, returning
`READY_FOR_PHASE5E1E_SIMFIN_COLLECTOR`. Phase 5-E1E is the **bounded, cache-safe
collector** that turns that proof into a prepared dataset.

It **loads each quarterly fundamentals dataset once**, filters the target universe
**locally**, stores the raw + normalized data under the **git-ignored**
`research/data/simfin/` tree, and emits **committed-safe** coverage / schema / quality
reports plus a **Phase 5-E2 enriched-model input plan**.

This phase **does not** train a model, **does not** deploy anything, and **does not**
build features. It only prepares the SimFin fundamentals dataset for the 5-E2 rerun.

This is **Track A quant work**. It touches no Paper Trader file, no GCP config, no
deploy path, no orders / automation, treats `D:` as read-only input only, and installs
nothing.

## Access method (no per-ticker web API)

The **only** access method is the official `simfin` package / bulk download
(`access_method = official_simfin_package_or_bulk`). Each dataset is loaded **once** with
`market='us'` and `variant='quarterly'`, then the universe is filtered **locally**
(in-memory) — **never one request per ticker**. There is no custom per-ticker web code in
this module.

## Universe

The target universe is the **Phase 5-C cross-sectional universe** (~128 large-cap names),
read from the committed `research/output/phase5c_feature_panel_sample.csv` `ticker`
column. Banks (which use SimFin's bank statement templates — JPM, BAC, C, WFC, USB, PNC,
…) are **discovered at load time, not assumed**: a ticker is routed to the bank or
standard template by where its quarterly statements actually appear. If the package ships
no dedicated `*_banks` loaders, banks are collected via the standard datasets and
`bank_template_separate` is recorded `false`.

## Datasets

| Standard | Bank | Required |
|----------|------|----------|
| Companies (shared) | — | required |
| Income Statement | Income Statement (Banks) | required |
| Balance Sheet | Balance Sheet (Banks) | required |
| Cash Flow | Cash Flow (Banks) | required |
| Derived Figures & Ratios | Derived Figures & Ratios (Banks) | **optional** |

Derived ratios are **optional and non-blocking**: if they are unavailable the collector
marks `derived_ratios_available=false` and the Phase 5-E2 plan notes that ratios can be
computed internally from the statements (`ratios_can_be_computed_internally=true`).

## Run it

**Dry-run (no key, no network):**
```powershell
python research\run_phase5e1e_simfin_free_collector.py
```
Requires no `SIMFIN_API_KEY`, performs no network calls, loads no dataset, emits the
collection plan + expected dataset map + all committed-safe artifacts, and recommends
`READY_FOR_SIMFIN_COLLECTOR_LIVE_RUN`.

**Live collection (requires the free key in the environment AND the `simfin` package):**
```powershell
# one-time package install into this project's venv (NOT done by the runner):
C:\Users\binis\paper_trader\.venv-win\Scripts\python.exe -m pip install simfin

$env:SIMFIN_API_KEY = "<your-free-simfin-key>"
python research\run_phase5e1e_simfin_free_collector.py --live --universe-source phase5c --max-tickers 128
```
The key is read from `SIMFIN_API_KEY` only — never pasted into source, never committed,
never printed (only the boolean `api_key_present`). It is passed **only** to
`simfin.set_api_key()`; it never appears in a URL, log line, or artifact.

## Recommendations (the six allowed values)

| Value | When |
|-------|------|
| `READY_FOR_SIMFIN_COLLECTOR_LIVE_RUN` | dry-run completed; the live collection is ready |
| `READY_FOR_PHASE5E2_ENRICHED_MODEL_RERUN` | live collection covered ≥ 80% of the universe with quarterly IS/BS/CF |
| `NEEDS_MORE_SIMFIN_COVERAGE` | live collection covered some, but < 80% of the universe |
| `BLOCKED_MISSING_SIMFIN_KEY` | `--live` without `SIMFIN_API_KEY` |
| `BLOCKED_NEEDS_SIMFIN_PACKAGE` | `--live` but the `simfin` package is not installed |
| `USE_SEC_LOCAL_FALLBACK` | live collection returned no quarterly fundamentals at all |

## Artifacts

**Committed-safe (summaries only — no payloads, no key) under
`research/output/phase5e1e_simfin_free_collector/`:**

- `phase5e1e_simfin_free_collector.json` — the full report (mode, key/package presence,
  free-tier facts, universe source/size, datasets loaded, dataset row counts, standard /
  bank / total coverage counts, derived-ratio availability, point-in-time safety,
  file counts, recommendation + next phase, safety contract).
- `simfin_dataset_collection_plan.csv` — one row per dataset (load once, filter locally).
- `simfin_universe_coverage.csv` — per ticker: resolved template, present statement
  codes, coverage status.
- `simfin_schema_catalog.csv` — per dataset: load status, columns detected, column list.
- `simfin_statement_row_counts.csv` — per dataset: full rows loaded vs rows after the
  local universe filter.
- `simfin_bank_vs_standard_coverage.csv` — standard vs bank covered names.
- `simfin_quality_report.csv` — per dataset: rows, tickers found, quarterly dates,
  min/max fiscal year, quality verdict.
- `simfin_point_in_time_readiness.csv` — the 12-month delay, ~5-year history, PIT
  alignment rule, and that live trading today is blocked.
- `simfin_secret_safety_audit.csv` — key-handling and git-ignore checks.
- `phase5e2_enriched_model_input_plan.json` — the next-phase enriched-model input design
  (join keys, PIT rule, planned feature families, bank handling, open questions).

**Git-ignored local data under `research/data/simfin/` (never committed):**

- `raw/phase5e1e/<template>/<code>.json` — filtered raw SimFin records (live only).
- `normalized/phase5e1e/<template>/<code>.csv` — flattened statement tables (live only).

`research/data/simfin/.gitignore` (already committed in 5-E1D) ignores `raw/`,
`normalized/`, and `*` (allowing only the `.gitignore` itself), so no payload or bulk
cache can ever be staged from there by accident.

## Point-in-time / live-trading note

The free tier is delayed ~12 months, so `usable_for_live_trading_today` is always
**false**; the data is only `point_in_time_safe_for_research`. Phase 5-E2 must align each
fundamental to its **data-availability date** (publish + free-tier lag), never the fiscal
period end, to avoid lookahead bias.

## Safety contract

`preview_only=true`; `orders_enabled`, `automation_enabled`, `broker_execution_enabled`,
`production_replacement`, `writes_to_d_drive`, `modifies_paper_trader`, `modifies_gcp`,
`installs_packages`, `trains_model`, `deploys` all `false`. Dry-run is offline and
key-less; live uses the env key only via `simfin.set_api_key()`, writes raw/normalized
only to the git-ignored data tree, never prints/writes the key, and never deletes data.
No FMP. No paid SimFin subscription. No commit, no push.
