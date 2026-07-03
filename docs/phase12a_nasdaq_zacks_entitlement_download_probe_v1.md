# Phase 12-A — Nasdaq Data Link Zacks Entitlement & Historical Estimates Download Probe (v1)

## 1. Why this phase exists

Phase 11-B4 ended at `ACTION_REQUIRED_ANALYST_REVISIONS_TRIAL`. The user then created a **free**
Nasdaq Data Link account, so `NASDAQ_DATA_LINK_API_KEY` is now in the environment. This phase
does **not** assume entitlement — it *tests* which Zacks datatables the free key can reach,
inspects their schema, and (the decisive question) determines whether the accessible data is a
genuine full-history entitlement or merely **Nasdaq's free premium SAMPLE**. Only genuine full
access can support a 545-name, ≥10-year, pre/post-2020 revisions backtest.

**No api key printed.** The key is read from the environment and never printed, logged, or written
to disk; every recorded URL is redacted to `api_key=***`. No orders, no automation, no broker, no
deploy, no GCP, no Paper Trader writes. Network is confined to the read-only Nasdaq Data Link
Tables API and only inside this phase's runner (a scoped exception to the usual offline rule).

## 2. Decision — `NASDAQ_ZACKS_NEEDS_CONTACT_SALES_OR_TRIAL`

The free key can **read the schema and a sample** of the Zacks tables, and the estimate-history
schema is a *perfect fit* for a revisions factor — but the data returned is **sample-only**
(a curated handful of tickers, a single year of history). The full universe/history needed for a
backtest is paid-gated. **The free account is insufficient; a paid subscription or self-serve
product trial is required.** This is not a vague "contact sales" — see the concrete next actions
in §6.

Decision enum: `NASDAQ_ZACKS_READY_FOR_FULL_DOWNLOAD` · `NASDAQ_ZACKS_CURRENT_ONLY_NOT_BACKTESTABLE`
· `NASDAQ_ZACKS_ENTITLEMENT_BLOCKED` · `NASDAQ_ZACKS_SCHEMA_BLOCKED` ·
**`NASDAQ_ZACKS_NEEDS_CONTACT_SALES_OR_TRIAL`** · `NASDAQ_ZACKS_NO_USABLE_TABLES`.

## 3. What the free key can and cannot access

| table | HTTP | schema class | obs date | verdict |
|---|:--:|---|:--:|---|
| ZACKS/EE   | 200 | CURRENT_SNAPSHOT_ONLY | — (only future `per_end_date` 2026–2030) | current consensus, **not** point-in-time |
| ZACKS/LTG  | 200 | CURRENT_SNAPSHOT_ONLY | — | long-term growth snapshot |
| ZACKS/MT   | 200 | MASTER_REFERENCE | — | master/reference table |
| ZACKS/EET  | 200 | CURRENT_SNAPSHOT_ONLY | — | estimate trend snapshot |
| **ZACKS/EEH**  | 200 | **REVISION_HISTORY** | `obs_date` | **ideal schema, SAMPLE-only** |
| **ZACKS/SEH**  | 200 | HISTORICAL_POINT_IN_TIME | `obs_date` | sales-est history, SAMPLE-only |
| **ZACKS/EREV** | 200 | **REVISION_HISTORY** | `eps_rev_date` | analyst-level revisions, SAMPLE-only |
| ZACKS/SE   | 200 | REVISION_HISTORY | `last_rev_date` | sales-est revisions, SAMPLE-only |
| ZACKS/ZEEH | 404 | — | — | code does not exist |
| ZACKS/EPRR | 404 | — | — | code does not exist |

**The point-in-time tables (`ZACKS/EEH`, `ZACKS/SEH`, `ZACKS/EREV`) carry exactly the fields a
revisions factor needs**, which is why this is a "buy it" result rather than "wrong data":

- **`ZACKS/EEH`** (EPS estimate history): `ticker`, `per_end_date` (fiscal period), **`obs_date`**
  (the point-in-time as-of date), `eps_mean_est`, `eps_median_est`, `eps_high_est`, `eps_low_est`,
  `eps_std_dev_est` (dispersion), `eps_cnt_est` (analyst count), **`eps_cnt_est_rev_up`** /
  **`eps_cnt_est_rev_down`** (the up/down revision counts — the net-revisions-momentum ingredients).
- **`ZACKS/EREV`** (estimate revisions): `broker_name`, `analyst_name`, **`eps_rev_date`** (new
  revision date), `eps_rev_est` (new estimate), `eps_rev_date_prev` / `eps_rev_est_prev` (prior).
- `ZACKS/EE` has **no `obs_date`** — its only date is the *future* fiscal `per_end_date` — so it is
  a current snapshot and cannot be used point-in-time on its own.

## 4. How we know it is a SAMPLE, not full access

Two independent, decisive checks (both read-only probes):

1. **Universe coverage.** Probing `ZACKS/EEH` across an evenly-spread sample of the 545-name
   universe, only **≈3 of 18 (~17%)** names return rows — a curated handful of mega-caps (AAPL,
   MSFT, JPM, XOM, MCD, CAT); most of the broad universe (A, AON, CSX, FDX, LLY, NEE, UBER, …)
   returns nothing. `EREV` coverage is ~11%, `SEH`/`SE` ~17%. An S&P 500 name like MOS returns
   **0 rows in every table**, and AAPL returns 0 in `EREV`. That is the signature of a curated
   sample ticker list, not full entitlement (`full_access` requires ≥50% coverage).
2. **History depth.** For a covered ticker (AAPL), `EEH.obs_date` is confined to **calendar-year
   2018 only** — `obs_date < 2016` returns nothing, the single page holds 533 rows spanning
   2018-01-01…2018-12-21 with no pagination. A pre/post-2020 subperiod backtest needs ≥10 years;
   one sample year cannot provide it.

The runner encodes these as `universe_coverage_frac ≥ 0.5` **and** `obs_date` history reaching
back to `2016-01-01`; a table passes `full_access` only if both hold. Every Zacks PIT table fails
both → `appears_sample = true`, `full_access = false`.

## 5. Alpha-readiness (schema fit vs. access)

`ZACKS/EEH` maps to the required revisions fields: ticker ✓, observation date (`obs_date`) ✓,
fiscal period ✓, EPS consensus ✓, analyst count ✓, high/low/std ✓, up-revision count ✓,
down-revision count ✓. So **`_SCHEMA_BACKTESTABLE = true`** but **`_FULL_ACCESS_NOT_SAMPLE = false`**,
hence **`_BACKTESTABLE = false`** — the schema is right, the access is not. The tradable construct
would be net-revisions momentum `(rev_up − rev_down)/cnt` + standardized Δ`eps_mean_est` over
30/60d, joined point-in-time on `obs_date ≤ entry_date` — the same PIT/subperiod harness as 11-C.

## 6. Concrete next action (not vague; not "email support")

1. **Subscribe to / trial the Zacks estimate-history product on Nasdaq Data Link.** The Zacks
   North American Earnings Estimates product page on `data.nasdaq.com` exposes a self-serve
   **"Trial This Product" / subscribe** button; that unlocks the full `ZACKS/EEH` + `ZACKS/EREV`
   history for the 545 universe.
2. **Confirm the paid tier covers `ZACKS/EEH` (EPS history) and `ZACKS/EREV` (analyst revisions).**
   Both are needed for a net-revisions-momentum factor at 63d.
3. **Alternate provider** if the Nasdaq trial is unavailable: **Intrinio Zacks** (estimate-trend /
   revision history via clean REST, self-serve trial, comparable PIT depth).
4. **Owned-key fallback:** the **FMP Premium** analyst-estimates upgrade (Phase 11-B4 rank-1) —
   `FMP_API_KEY` is already present, so only a tier upgrade is needed for a first revisions screen.

**The free Nasdaq account is insufficient.** Once a paid/trial entitlement is in place, re-run this
runner: on genuine full access it decides `NASDAQ_ZACKS_READY_FOR_FULL_DOWNLOAD` and continues
automatically to a bounded, resumable **Phase 12-B** full 545-universe download (raw under
`research/data/nasdaq_zacks/estimates/raw/`, normalized under `.../normalized/`, with manifest and
coverage reports), followed by an 11-C-style incremental alpha test under the RC1–RC8 criteria.
Phase 12-B is **not** started here (access is sample-only).

## 7. Artifacts (`research/output/phase12a_nasdaq_zacks_entitlement_download_probe/`)

`phase12a_nasdaq_zacks_entitlement_download_probe.json` · `probe_log.json` (redacted, key-free,
replayable) · `nasdaq_zacks_table_probe_results.csv` · `nasdaq_zacks_schema_inventory.csv` ·
`nasdaq_zacks_alpha_readiness_check.csv` · `nasdaq_zacks_sample_download_manifest.csv` ·
`nasdaq_zacks_blocked_tables.csv` · `nasdaq_zacks_next_action.csv`. Small raw samples are cached
under `research/data/nasdaq_zacks/estimates/raw/sample/` (schema evidence; no key).

## 8. Safety / constraints

Key read from env, **no api key printed** (redacted URLs, env NAME only). Live network limited to
the Nasdaq Data Link Tables API (read-only GET) in this runner only; `--offline` replays the cached
`probe_log.json` with zero network for deterministic tests. **No orders, no automation**, no broker,
no deploy, no GCP, no Paper Trader writes, no push. Any paid acquisition requires explicit user
opt-in.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python research/run_phase12a_nasdaq_zacks_entitlement_download_probe.py            # live probe
python research/run_phase12a_nasdaq_zacks_entitlement_download_probe.py --offline  # replay cache
python -m pytest tests/test_phase12a_nasdaq_zacks_entitlement_download_probe.py -q
```
