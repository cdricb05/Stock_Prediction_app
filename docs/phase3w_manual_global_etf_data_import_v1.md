# Phase 3-W — Manual Global ETF Price Data Intake and Repair Gate (v1)

Phase 3-W is a **research-only**, local, **manual** data-intake pack. It replaces Phase 3-V's
blocked network path with a manual one: the user supplies real CSV price files (browser export,
broker export, or a vendor export they already have) and this phase normalizes, validates, and
scores readiness for the cross-asset feature factory. It trains no model, creates no production
model candidate, computes no predictions / scores / portfolio weights / order instructions, and
writes no deployable model artifact.

Guardrails: it uses **no network** at all.
It does not deploy. It does not run migrations. It does not write to production DB. It does not trade.
It restarts no prediction service, enables no model-v2 serving flag, reads/writes nothing on the data
drive, reads no provider API key, and places no orders. It does **not** call Stooq, yfinance, Alpha Vantage, FRED, or any paid vendor API. No
production edge is claimed; the universe is current-as-of, so downstream results remain
survivorship-biased. Price rows are **never faked**: invalid rows are dropped, missing rows are
**not** fabricated, and the only sample rows shipped are clearly labelled `SAMPLE` in the template.

## Why this phase

Phase 3-V recommended `GLOBAL_ETF_PRICE_DATA_BLOCKED_DOWNLOAD_FAILURE`: Stooq gated its free CSV
endpoint behind a browser-verification challenge and denied automated downloads, so 0 of 22 proxies
were acquired. Rather than evade that block, Phase 3-W opens a clean, policy-compliant **manual**
intake: the user places real CSV exports in a local folder and this phase does the normalization and
readiness scoring that Phase 3-V would have done on downloaded data.

## Where to put manual CSV files

Place files directly in:

    research/input/global_assets/manual/

Two accepted layouts:

1. **One file per ticker**, named `<TICKER>.csv` — e.g. `SPY.csv`, `QQQ.csv`, `TLT.csv`.
2. **One combined file** named `global_etf_prices.csv` with a `ticker` column.

`README.md` and `manual_global_etf_price_template.csv` in that folder are ignored by the scan.

## Accepted column variants (header row, case-insensitive)

    Date, Open, High, Low, Close, Adj Close, Volume
    date, open, high, low, close, adjusted_close, volume
    ticker, date, open, high, low, close, adjusted_close, volume
    ticker, date, adjusted_open, adjusted_high, adjusted_low, adjusted_close, volume

`adjusted_close` uses `adjusted_close`/`Adj Close` if present, otherwise `close`. When a variant has
only adjusted columns (no plain `close`), `close` is filled from `adjusted_close`. Rows with a
missing/invalid date or a missing/non-positive `adjusted_close` are dropped; duplicate dates per
ticker are dropped.

## Normalized output schema

    ticker, date, open, high, low, close, adjusted_close, volume, source_file, source_type

## Decision rule

Let `F` be the number of non-template manual CSV files found, `U` the number of target proxies that
parsed to **usable** history (≥ 250 clean daily rows), and `C` the number of distinct asset classes
those cover:

| Condition | Recommendation |
| --- | --- |
| `F == 0` | `MANUAL_GLOBAL_ETF_DATA_WAITING_FOR_FILES` |
| `U >= 12` and `C >= 5` | `MANUAL_GLOBAL_ETF_DATA_READY` |
| `U >= 5` (but not READY) | `MANUAL_GLOBAL_ETF_DATA_PARTIAL` |
| files present but `U < 5` | `MANUAL_GLOBAL_ETF_DATA_BAD_FORMAT` |

The recommended next phase is always **3-X** (READY → *Build Cross-Asset Feature Factory*;
PARTIAL → *Complete Manual Global ETF Data Pack*; WAITING → *Add Manual Global ETF Price CSVs*;
BAD_FORMAT → *Repair Manual Global ETF CSV Formats*).

## This run (current local state)

| Metric | Value |
| --- | --- |
| Target proxies | 22 |
| Manual files found | 0 |
| Usable proxies (≥ 250 rows) | 0 |
| Missing / not usable | 22 |
| Asset classes covered (usable) | 0 of 8 |
| Panel rows | 0 |
| Feature families ready | 0 of 8 |
| Recommendation | `MANUAL_GLOBAL_ETF_DATA_WAITING_FOR_FILES` |
| Recommended next phase | `3-X` — Add Manual Global ETF Price CSVs |

On the first run the folder is empty (the phase only creates the `README.md` and the `SAMPLE`
template), so the honest outcome is `WAITING_FOR_FILES`. To advance: export the 22 ETF histories
from a legitimate source you have access to, drop them into
`research/input/global_assets/manual/` using an accepted layout/format, and re-run
`python -B research/run_phase3w_manual_global_etf_data_import.py`. `manual_download_instructions.csv`
lists the target filename, required columns, and acceptable manual sources per ticker.

## Outputs

- `research/output/phase3w_manual_global_etf_data_import.json` — full result + safety flags.
- `phase3w_manual_global_etf_data_import/manual_file_inventory.csv`
- `phase3w_manual_global_etf_data_import/normalized_global_etf_price_panel.csv`
- `phase3w_manual_global_etf_data_import/global_etf_price_coverage.csv`
- `phase3w_manual_global_etf_data_import/global_etf_price_quality.csv`
- `phase3w_manual_global_etf_data_import/missing_or_bad_format_files.csv`
- `phase3w_manual_global_etf_data_import/manual_download_instructions.csv`
- `phase3w_manual_global_etf_data_import/cross_asset_feature_readiness.csv`
- `phase3w_manual_global_etf_data_import/readiness_decision_table.csv`
- `research/input/global_assets/manual/README.md` + `manual_global_etf_price_template.csv`.

## Guarantees

All price data is **real and never faked**: every normalized row comes from a CSV the user supplied,
invalid rows are dropped, and missing history is recorded as missing rather than invented. There is
**no network** call of any kind; no Stooq, yfinance, Alpha Vantage, FRED, or paid vendor API; and
nothing is read from or written to the data drive. Every output file is Git-safe (well under 50 MB).
This phase is **research-only** and claims no production edge.
