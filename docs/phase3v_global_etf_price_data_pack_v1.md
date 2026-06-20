# Phase 3-V — Global ETF Price Data Pack (v1)

Phase 3-V is a **research-only**, local **data-preparation** pack. It acquires and normalizes real
daily price history for the Phase 3-U global cross-asset ETF proxy universe using free public
**Stooq** CSV downloads only. It trains no model, creates no production model candidate, computes no
predictions / scores / portfolio weights / order instructions, and writes no deployable model
artifact.
Guardrails: it does not deploy. It does not run migrations. It does not write to production DB. It does not trade.
It restarts no prediction service, enables no model-v2 serving flag, writes nothing to the data
drive, reads no provider API key, and places no orders. The only network source contacted is
**Stooq** (`https://stooq.com/q/d/l/?s=<lowercase_ticker>.us&i=d`): it does not use the yfinance
library, does not call Alpha Vantage, does not call FRED, and calls no paid vendor API. No
production edge is claimed; the universe is current-as-of, so downstream results remain
survivorship-biased. Price rows are **never faked**: invalid rows are dropped and missing rows are
**not** fabricated.

## Why this phase

Phase 3-U recommended `GLOBAL_ASSET_UNIVERSE_BLOCKED_NEEDS_LOCAL_PRICE_DATA`: only 1 of the 22
target cross-asset proxies (SPY) had a local daily price history, blocking the cross-asset feature
factory. Phase 3-V acquires the missing local price data — the exact next step Phase 3-U named —
from a single free public source (Stooq), normalizes it to the project's common OHLCV schema, and
measures whether enough usable history now exists locally to build the feature factory.

## What it does

1. **Confirms Phase 3-U** — reads the Phase 3-U result (read-only; Phase 3-U outputs are never
   modified), confirms `phase == "3-U"` and that its recommendation was to acquire local price data
   (`GLOBAL_ASSET_UNIVERSE_BLOCKED_NEEDS_LOCAL_PRICE_DATA` or `..._PARTIAL_LOCAL_COVERAGE`), and
   loads `target_global_asset_universe.csv` as the source of target tickers.

2. **Downloads from Stooq only** — for each target builds the Stooq symbol `<lowercase>.us` and
   downloads `https://stooq.com/q/d/l/?s=<symbol>&i=d` to
   `research/input/global_assets/stooq/<TICKER>.csv` using the Python standard library
   (`urllib.request`). Each ticker is retried at most twice, with a small sleep between requests.
   HTTP / download errors are recorded per ticker and never crash the run. No non-Stooq domain is
   contacted.

3. **Normalizes** — maps Stooq's `Date,Open,High,Low,Close,Volume` to the common schema
   `ticker,date,open,high,low,close,adjusted_close,volume,source`. Because Stooq's daily CSV has no
   adjusted-close column, **`adjusted_close` mirrors `close`**. Dates are ISO; numeric columns are
   coerced to numbers; rows with a missing/invalid date or a missing/non-positive close are dropped;
   duplicate dates are dropped. No rows are fabricated.

4. **Writes outputs** — a download manifest, the combined price panel, per-ticker coverage and
   quality tables, a missing/failed-downloads table, a cross-asset feature-readiness table, and a
   readiness decision table — plus the result JSON.

## Decision rule

Let `U` be the number of target proxies downloaded **and usable** (a saved Stooq CSV with at least
250 clean daily rows) and `C` the number of distinct asset classes those cover:

| Condition | Recommendation |
| --- | --- |
| `U >= 12` and `C >= 5` | `GLOBAL_ETF_PRICE_DATA_READY` |
| `U >= 5` (but not READY) | `GLOBAL_ETF_PRICE_DATA_PARTIAL_DOWNLOAD` |
| `U < 5` | `GLOBAL_ETF_PRICE_DATA_BLOCKED_DOWNLOAD_FAILURE` |

In every case the recommended next phase is **3-W** (READY → *Build Cross-Asset Feature Factory*;
PARTIAL → *Repair Missing Global ETF Price Data*; BLOCKED → *Repair Phase 3-V Downloads*).

## This run (current local state)

| Metric | Value |
| --- | --- |
| Target proxies | 22 |
| Downloaded (saved Stooq CSV) | 0 |
| Usable (>= 250 clean daily rows) | 0 |
| Missing / not usable | 22 |
| Asset classes covered (usable) | 0 of 8 |
| Combined panel rows | 0 |
| Feature families ready | 0 of 8 |
| Recommendation | `GLOBAL_ETF_PRICE_DATA_BLOCKED_DOWNLOAD_FAILURE` |
| Recommended next phase | `3-W` — Repair Phase 3-V Downloads |

**Why blocked (faithfully recorded, not faked):** Stooq has placed its free CSV download endpoint
behind a JavaScript **browser-verification challenge** (a SHA-256 proof-of-work that posts to a
same-origin `/__verify` endpoint). A standard-library `urllib` request receives that challenge page
instead of CSV; and even after the challenge is satisfied, the `q/d/l/` download endpoint returns
**"Access denied"** for this environment — Stooq refuses automated CSV downloads from it. All 22
tickers therefore record `downloaded = False` with the exact Stooq response classified in the
manifest's `error` column (browser-verification challenge / access denied). No price rows were
fabricated to paper over the block. Phase 3-W should repair the downloads from a context Stooq
serves (e.g., an interactive/allow-listed network or a manual CSV export placed under
`research/input/global_assets/stooq/`), then re-run this same normalization pack.

## Outputs

- `research/output/phase3v_global_etf_price_data_pack.json` — full result + safety flags.
- `phase3v_global_etf_price_data_pack/download_manifest.csv`
- `phase3v_global_etf_price_data_pack/global_etf_price_panel.csv`
- `phase3v_global_etf_price_data_pack/global_etf_price_coverage.csv`
- `phase3v_global_etf_price_data_pack/global_etf_price_quality.csv`
- `phase3v_global_etf_price_data_pack/missing_or_failed_downloads.csv`
- `phase3v_global_etf_price_data_pack/cross_asset_feature_readiness.csv`
- `phase3v_global_etf_price_data_pack/readiness_decision_table.csv`
- Raw per-ticker Stooq CSVs under `research/input/global_assets/stooq/`.

## Guarantees

All price data is **real and never faked**: every row comes from a Stooq daily CSV, invalid rows are
dropped, and missing history is recorded as a failed/missing download rather than invented. The only
network source is Stooq; no yfinance, Alpha Vantage, FRED, or paid vendor API is called, and nothing
is written to the data drive. Every output file is Git-safe (well under 50 MB). This phase is
**research-only** and claims no production edge.
