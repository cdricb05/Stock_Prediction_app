# Phase 11-B0 — Local Existing Data Entitlement Probe (v1)

## 1. Why this phase exists

Phase 11-A concluded (design only) that a genuinely **stronger** alpha than the modest 10-D quality
baseline needs **new orthogonal data**, and ranked analyst estimate revisions first. Before recommending
any **paid** acquisition, this probe inventories what is **already on disk** and which provider keys are
**already entitled** in this shell — a prior collection pass (or the user) may already have downloaded
usable orthogonal data. This runner makes **no api calls**, downloads nothing, reads **no secret**
values (only environment-variable *names*), and writes nothing outside its own output folder. It creates
**no orders** and **no automation**.

## 2. What the probe found

**Entitled provider keys present (names only):** `EODHD`, `FMP`, `FINNHUB`, `POLYGON`,
`ALPHAVANTAGE`, `NASDAQ_DATA_LINK`, `TIINGO`, `FRED`. This is a material change from prior autonomous
sessions (which had no keys): keyed free-tier / entitled download is now possible.

**Local candidate orthogonal families (measured coverage):**

| family | provider | tickers | med obs/ticker | months | span | class | prior status |
|---|---|---:|---:|---:|---|---|---|
| **insider_sentiment_mspr** | finnhub | 292 | 76 | 125 | 2016-02→2026-06 | **BACKTESTABLE** | NEW, never tested |
| analyst_recommendation_change | finnhub | 448 | 4 | 8 | 2022-10→2026-06 | SHALLOW_SNAPSHOT | new but too thin/name |
| analyst_estimate_revision_av | alphavantage | 23 | 111 | 319 | 1996→2026 | TOO_SPARSE | 11-A #1 family, rate-capped |
| analyst_estimates_fmp | fmp | ~8 | — | — | — | TOO_SPARSE | 11-A #1 family, tier-capped |
| short_interest_days_to_cover | polygon | 545 | 10 | 102 | 2017-12→2026-06 | SHALLOW / — | **rejected in 10-A** (t=1.56) |

## 3. Readiness classification (a-priori thresholds, never tuned to a result)

A family is **BACKTESTABLE** only if it is broad and deep enough for a sector-neutral walk-forward test:
`unique_tickers ≥ 100`, `median_obs_per_ticker ≥ 12`, `distinct_months ≥ 24`. The **median-obs-per-ticker**
guard is the decisive one: it rejects broad-but-shallow snapshots that a naive ticker-count would pass.

- **insider_sentiment_mspr** — 292 names, median **76** monthly observations/ticker over **125** months
  (pre- and post-2020) → **BACKTESTABLE**. A genuinely new, orthogonal (insider positioning ≠ realized
  fcf/accruals levels), never-tested family. This is the primary Phase 11-C candidate.
- **analyst_recommendation_change** — broad (448 names) but only **4** months per name (Finnhub free tier
  returns a short recommendation-trend window) → **SHALLOW_SNAPSHOT**, not walk-forward testable.
- **analyst_estimate_revision_av / analyst_estimates_fmp** — the Phase 11-A **#1** family, but only ~23
  (AlphaVantage, ~25 req/day cap) and ~8 (FMP tier cap) names locally → **TOO_SPARSE** for a 545-universe
  cross-section. Its full-universe depth is **paid-gated**.
- **short_interest_days_to_cover** — broad but the **family was already rejected in Phase 10-A** (best
  t=1.56) on levels; only a *new derived field* (change / days-to-cover momentum) could be a legitimate
  re-test, so it is **held out** of the ready set.

## 4. Decision — `LOCAL_DATA_READY_FOR_ALPHA_TEST`

At least one **new** orthogonal family already on disk (insider sentiment MSPR) is broad and deep enough
for a rigorous walk-forward alpha test against `composite_sn`. The queue therefore proceeds to provider
discovery (11-B1), a bounded keyed top-up (11-B2), a formal readiness gate (11-B3), and the actual alpha
test (11-C) — rather than stopping at a paid shopping cart. The **paid-gated** analyst-estimate-revision
depth is recorded for the 11-B4 cart if 11-C finds no local alpha.

Decision enum: `LOCAL_DATA_READY_FOR_ALPHA_TEST` · `EXISTING_KEYS_READY_FOR_DOWNLOAD` ·
`LOCAL_SNAPSHOT_ONLY_NOT_BACKTESTABLE` · `NO_LOCAL_OR_ENTITLED_DATA_FOUND`.

## 5. Artifacts (`research/output/phase11b0_local_data_entitlement_probe/`)

`phase11b0_local_data_entitlement_probe.json` · `local_data_inventory.csv` · `env_key_presence.csv`
(names + boolean only) · `family_readiness.csv`.

## 6. Safety / constraints

Offline (filesystem + `os.environ` name-presence only). **No api calls**, **no provider probing** of any
network endpoint, **no secret** values read or emitted, no new data purchased, no Paper Trader writes,
**no orders**, **no automation**, no broker, no deploy, no GCP. The modest 10-D baseline is not oversold.
Commit only the phase11b0 files if targeted tests pass. No push.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python -m py_compile research/run_phase11b0_local_data_entitlement_probe.py
python research/run_phase11b0_local_data_entitlement_probe.py
python -m pytest tests/test_phase11b0_local_data_entitlement_probe.py -q
```
