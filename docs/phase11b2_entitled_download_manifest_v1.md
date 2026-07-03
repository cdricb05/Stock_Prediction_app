# Phase 11-B2 — Free / Currently-Entitled Data Manifest (v1)

## 1. Why this phase exists

The mission's 11-B2 step is "download every relevant free / currently-entitled dataset." Phase 11-B0
found a prior entitled-key pass had **already** downloaded the relevant free / free-tier orthogonal data,
and Phase 11-C proved the broad + deep members do **not** beat the baseline. This phase does the honest,
non-wasteful thing: it **manifests** what is already on disk (provenance, coverage, schema) and records
the **free-tier ceilings** that block a free expansion of the highest-priority family (analyst estimate
revisions) — the evidence that the remaining blocker is a **paid** entitlement.

It does **not** re-download and makes **no api calls**: a fresh free pull of the two broad free families
(insider sentiment, short interest) cannot change the 11-C negative result, and the priority family has no
free path to 545-name point-in-time revision depth. It creates **no orders** and **no automation**.

## 2. What is already on disk (entitled / free)

| family | provider | raw files | normalized | tickers | note |
|---|---|---:|:--:|---:|---|
| insider_sentiment_mspr | Finnhub | 322 | ✓ | 292 | backtestable; tested 11-C → no alpha |
| analyst_recommendation_change | Finnhub | 452 | ✓ | 448 | shallow (~4 mo/name) → not backtestable |
| analyst_estimate_revision_av | AlphaVantage | 23 | ✓ | 23 | rate-capped (25/day) |
| analyst_estimates_fmp | FMP | 8 | — | — | tier-capped |
| short_interest_days_to_cover | Polygon | 545 | ✓ | 545 | family rejected 10-A; re-tested 11-C → no alpha |
| earnings_av | AlphaVantage | 1 | — | — | earnings snapshot |

## 3. Free-tier ceilings (the paid-gate evidence)

- **AlphaVantage** — 25 requests/day → only **23** of 545 names; ~22 days to cover the universe at the free
  cap, and PIT revision *history* isn't delivered → **paid tier required**.
- **FMP** — analyst-estimates endpoint premium-gated on the current key tier → only **8** names → **paid
  tier required**.
- **Finnhub** recommendation-trend — returns only the most recent ~4 monthly snapshots/name → broad but
  too shallow for a walk-forward test (11-B0 `SHALLOW_SNAPSHOT`).
- **Finnhub** insider sentiment — broad + deep (292 names, 10yr) and backtestable → tested 11-C → **no
  incremental alpha**.
- **Polygon** short interest — full universe → re-tested (change field) 11-C → **no incremental alpha**.

## 4. Decision — `PARTIAL_FREE_DATA_LOADED`

Substantial free / currently-entitled data is loaded, but it is exhausted for a stronger alpha: the broad
families don't beat the baseline (11-C) and the priority analyst-revision family is **free-tier-capped** and
cannot be expanded to universe depth without **paid** access. The blocker is handed to Phase 11-B4.

Decision enum: `FREE_DATA_LOADED` · `PARTIAL_FREE_DATA_LOADED` · `NO_FREE_DATA_LOADABLE` ·
`DOWNLOAD_BLOCKED`.

## 5. Artifacts (`research/output/phase11b2_entitled_download_manifest/`)

`phase11b2_entitled_download_manifest.json` · `data_manifest.csv` · `coverage_report.csv` ·
`free_tier_ceilings.csv`.

## 6. Safety / constraints

Offline (filesystem inventory only). **No api calls**, no re-download, no secret values, no purchase, no
Paper Trader writes, **no orders**, **no automation**, no broker, no deploy, no GCP, no payment. Commit
only the phase11b2 files if tests pass. No push.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python research/run_phase11b2_entitled_download_manifest.py
python -m pytest tests/test_phase11b2_entitled_download_manifest.py -q
```
