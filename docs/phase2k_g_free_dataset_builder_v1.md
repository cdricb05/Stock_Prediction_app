# Phase 2K-G — Free Expanded Price/Volume Dataset Builder (v1)

_Implemented by `research/build_phase2k_g_free_expanded_dataset.py` and validated by
`research/analyze_phase2k_g_free_dataset_builder.py`. This phase creates the builder and a
small sample ticker universe and proves they work using only safe, offline modes. It does
**not** run a real download, build no large dataset, train no model, and claim no
**production edge**._

> Scope: this phase writes an import-safe builder CLI, a sample ticker universe, an
> offline-only readiness validator, and one small validation JSON. It is research tooling
> only: it **does not deploy**, it **does not restart stock-api.service**, it
> **does not enable** the model-v2 serving flag, it **does not run migrations**, it
> **does not write to production DB**, and it **does not trade**. No order placement, no
> automation, no model training, and no live data download in tests or validation, and it
> claims no **production edge**.

## Why Phase 2K-G follows Phase 2K-F

Phase 2K-F converted the Phase 2K-E feasibility result into a concrete, deferred build plan
for a free, survivorship-caveated expanded price/volume dataset and routed to **Phase 2K-G —
Free Expanded Price/Volume Dataset Builder**, with `create_build_script_now = false` and
`execute_data_build_now = false`. Phase 2K-G implements that planned builder. It still does
not run the full live build: that remains a separate, explicit, manual step (Phase 2K-H).

## What was created

| File | Role |
|---|---|
| `research/build_phase2k_g_free_expanded_dataset.py` | Import-safe builder CLI with pure, testable helpers and three explicit modes. |
| `research/input/phase2k_g_free_universe_tickers_sample.csv` | Small sample ticker universe (14 liquid names + SPY) with the required columns and a survivorship caveat per row. |
| `research/analyze_phase2k_g_free_dataset_builder.py` | Offline readiness validator: runs the builder only in safe modes and writes one validation JSON. |
| `research/output/phase2k_g_builder_validation.json` | The validator's output (produced when the analyzer is run manually). |
| `tests/test_phase2k_g_free_dataset_builder.py` | Self-running tests (no pytest required). |
| `docs/phase2k_g_free_dataset_builder_v1.md` | This document. |

The builder exposes the planned pure helpers: `load_universe`, `validate_universe`,
`build_survivorship_caveat`, `normalize_price_history`, `compute_basic_features`,
`run_data_quality_checks`, `build_summary`, `dry_run`, `fixture_run`, and `main`.

## Safe modes

The builder defaults to a safe mode. Work only happens behind an explicit flag:

- **`--dry-run` (default).** Validates the configuration and the planned output paths, reads
  the ticker universe, and prints a plan summary. It makes no network call and writes none of
  the large output files.
- **`--fixture-mode`.** Runs the normalize → feature → data-quality → summary transform on a
  tiny in-memory fixture (two names plus the benchmark over a handful of days) and writes the
  resulting small artifacts **only into a caller-provided temp directory**. It refuses to
  write into the real `research/output` directory and makes no network call.
- **`--execute` + `--allow-network`.** The real free build, intended for the **manual** Phase
  2K-H step. `--execute` requires the additional explicit `--allow-network` guard; if
  `--allow-network` is absent the builder fails safely with a clear message and fetches
  nothing. The retrieval library is imported lazily inside the guarded path so importing the
  module stays network-free. This mode is never run by the tests or the analyzer.

## Why validation does not run a real download

Readiness is proved without touching the network. The analyzer reads the Phase 2K-F plan,
confirms the builder and the sample universe exist, runs the builder **only** in dry-run and
fixture modes (the fixture writes into a temp directory), confirms the large real output
files were not created, and writes one validation JSON. Keeping the validation offline keeps
the safety surface small and makes the tests deterministic: no network, no large files, no
model. The full live download is deliberately separated into the explicit manual Phase 2K-H
run.

## Sample universe vs. real universe

The committed `…_sample.csv` is a **sample only** — 14 liquid U.S. names plus the SPY
benchmark — enough to exercise validation and the dry-run path. It is not the production
universe. The full local / manual universe file
`research/input/phase2k_g_free_universe_tickers.csv` (≥100 liquid names per the Phase 2K-F
scope) is assembled manually for the Phase 2K-H run and is **not** created in this phase. Both
files carry the same columns — `ticker, name, sector, source, active_as_of,
survivorship_caveat` — and a current-as-of membership list is acceptable only with the
survivorship caveat recorded.

## External data storage (D: drive, not the C: repo)

Large expanded datasets, cache files, and the real Phase 2K-G / 2K-H build outputs are
intentionally stored **outside** the C: repository, on the dedicated data drive rooted at
`D:\Stock_Prediction_app_data\phase2k_g`:

- real outputs → `D:\Stock_Prediction_app_data\phase2k_g\output\`
- retrieval cache → `D:\Stock_Prediction_app_data\phase2k_g\cache\`
- the full (non-sample) ticker universe → `D:\Stock_Prediction_app_data\phase2k_g\input\phase2k_g_free_universe_tickers.csv`

The C: repo keeps only lightweight, source-controlled files: the builder script, the analyzer,
the tests, this doc, the small sample universe CSV, and the small validation JSON. The data
root is configurable through the builder's `--data-root` flag (default
`D:\Stock_Prediction_app_data\phase2k_g`); `--execute` derives its output directory as
`<data-root>\output` and its cache directory as `<data-root>\cache` unless explicitly
overridden with `--output-dir` / `--cache-dir`. The dry-run mode and the analyzer never create
any folder on the D: drive — only `--execute --allow-network` may.

## Planned real outputs (declared, not created here)

The full build would write five large files under `<data-root>\output` (default the D: data
root); the dry-run and fixture modes never create them, and the tests assert they remain absent
on both the D: data root and under the C: repo:

- `D:\Stock_Prediction_app_data\phase2k_g\output\phase2k_g_expanded_price_history_free.csv`
- `D:\Stock_Prediction_app_data\phase2k_g\output\phase2k_g_expanded_scored_free.csv`
- `D:\Stock_Prediction_app_data\phase2k_g\output\phase2k_g_data_quality_report.json`
- `D:\Stock_Prediction_app_data\phase2k_g\output\phase2k_g_data_build_summary.json`
- `D:\Stock_Prediction_app_data\phase2k_g\output\phase2k_g_survivorship_caveat.json`

## Data-quality checks

`run_data_quality_checks` runs the fixed 14-check catalog on the assembled panel: adjusted-
close continuity; duplicate `(date, ticker)` rows; missing adjusted close; missing volume;
outlier daily returns; zero/negative prices; zero/negative volume where unexpected;
ticker-level date coverage; benchmark coverage; minimum years achieved; minimum ticker count
achieved; no forward-filled labels; no point-in-time membership claim; and survivorship
caveat present. A hard-check failure yields `FAIL`; otherwise any caveat — including the
always-present survivorship caveat — yields `PASS_WITH_CAVEAT`; a fully clean panel yields
`PASS`. A survivorship-caveated free build is therefore never reported as a clean `PASS`.

## Survivorship caveat

`build_survivorship_caveat` records that membership is a **current-as-of** approximation,
never a point-in-time constituent claim (`point_in_time_membership_claimed = false`), that any
retest on the panel must be reported as survivorship-biased, and that a clean point-in-time
build is deferred to a later paid-source decision. This carries the Phase 2K-F policy forward.

## How Phase 2K-H should run the manual live build

`recommended_next_phase` routes to **Phase 2K-H — Manual Free Expanded Dataset Build Run**:
manually assemble the full local ticker universe on the D: data root, then run the builder with

```powershell
python research/build_phase2k_g_free_expanded_dataset.py --execute --allow-network `
  --data-root "D:\Stock_Prediction_app_data\phase2k_g" `
  --universe "D:\Stock_Prediction_app_data\phase2k_g\input\phase2k_g_free_universe_tickers.csv" `
  --start 2016-01-01 --end 2026-01-01
```

to fetch the free daily adjusted OHLCV + volume, assemble the panel, run the data-quality
checks and pass/fail gates, and write the five real outputs into
`D:\Stock_Prediction_app_data\phase2k_g\output`. Even then it **does not deploy**,
**does not enable** the model-v2 flag, **does not write to production DB**, **does not run
migrations**, **does not trade**, and claims no **production edge**.

## Robust retrieval and loud build failure (BUILD_OK vs. refusal)

The first manual `--execute` run exited 0 while producing `rows=0 dq=FAIL`, so a completely
empty dataset was almost mistaken for a good build. The builder now guards against that:

- **Robust per-ticker normalization.** `_normalize_yf_frame` handles single-level columns,
  multi-index `(field, ticker)` columns, and a close-only frame (Open/High/Low safely mirror
  the adjusted close; volume stays missing rather than being forward filled). A single ticker
  returning an unexpected shape no longer raises through the loop and silently empties the
  whole build.
- **Individual ticker failures are allowed and reported.** A failed or empty ticker (e.g. MMC)
  is recorded — it does **not** abort the run — and the per-run retrieval diagnostics
  (`requested_ticker_count`, `successful_ticker_count`, `failed_ticker_count`,
  `failed_tickers`, `empty_tickers`, `rows_downloaded`) are printed to stdout and embedded in
  the build summary JSON as `retrieval_diagnostics`.
- **A bad live build fails loudly.** `evaluate_build_result` treats a build as **failed** when
  the normalized `row_count == 0` **or** `data_quality_status == FAIL`. In that case
  `execute_build` still writes the five outputs (so the failed run can be inspected), marks the
  summary `build_ok = false`, prints a clear refusal message, and the CLI **exits non-zero** so
  PowerShell never reports it as `BUILD_OK`.
- **A successful command must create non-empty data with a passing data quality.** The only
  acceptable result is a positive row count together with a `PASS` or `PASS_WITH_CAVEAT`
  data-quality status. The large outputs always remain on
  `D:\Stock_Prediction_app_data\phase2k_g`, never in the C: repo.

## Why no model candidate is created yet

This phase builds tooling and data plumbing, not models. The Phase 2K-A model-candidate gate
stays locked: no model candidate may be created until a feature family passes the IC sweep and
the out-of-sample robustness / walk-forward battery on real data spanning more than one market
regime, with no single-regime artifact, no excessive ticker concentration, and no look-ahead
leakage. That multi-regime panel does not exist until the manual 2K-H build produces it. Phase
2K-G trains nothing, fits nothing, and creates no model candidate. The model-v2 serving flag
stays disabled.

## Safety flags (from the validation JSON)

```
database_touched        = false
database_write_executed = false
migration_executed      = false
deployment_executed     = false
model_v2_enabled        = false
production_edge_claimed = false
no_trading              = true
no_orders               = true
no_automation           = true
```

The `decision` block additionally records `live_download_executed = false`,
`real_dataset_outputs_created = false`, `paid_data_acquired = false`, and
`model_candidate_created = false`, with `builder_script_created = true` and
`sample_universe_created = true`.

## Conclusion

Phase 2K-G turns the Phase 2K-F plan into a working, import-safe builder and proves it offline
with a sample universe and a tiny fixture — creating no large dataset, fetching nothing,
training nothing, and claiming no **production edge**. The disciplined next step is the manual
free build (Phase 2K-H), still no purchase and still no model.
